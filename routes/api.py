from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from model import Lead, Customer, db
from utils.gst import validate_gst
import os
import requests

api_bp = Blueprint('api', __name__, url_prefix='/api')


def get_sandbox_token(api_key, api_secret):
    """
    Authenticate with Sandbox.co.in and return an access token.
    Docs: POST https://api.sandbox.co.in/authenticate
    """
    try:
        headers = {
            "x-api-key": api_key,
            "x-api-secret": api_secret,
            "x-api-version": "1.0.0"
        }
        resp = requests.post(
            "https://api.sandbox.co.in/authenticate",
            headers=headers,
            timeout=15
        )
        res_json = resp.json()

        if resp.status_code == 200 and "access_token" in res_json:
            return res_json["access_token"], None

        # Sandbox wraps token inside 'data' in some versions
        if resp.status_code == 200 and "data" in res_json:
            token = res_json["data"].get("access_token")
            if token:
                return token, None

        # Extract error message
        error_msg = (
            res_json.get("message")
            or res_json.get("error", {}).get("message", "")
            or resp.text[:200]
        )
        return None, f"Auth Failed ({resp.status_code}): {error_msg}"

    except requests.exceptions.Timeout:
        return None, "Authentication timed out. Please try again."
    except requests.exceptions.ConnectionError:
        return None, "Cannot connect to GST API server. Check your internet connection."
    except Exception as e:
        return None, f"Auth Error: {str(e)}"


@api_bp.route('/gst/<gst_number>', methods=['GET'])
@login_required
def get_gst_details(gst_number):
    """
    Fetch GST details by GSTIN using Sandbox.co.in API.
    Flow: Authenticate → POST search → Parse & return.
    """
    if not gst_number:
        return jsonify({"error": "GST number is required"}), 400

    # 1. Normalize and validate format
    gst_number = gst_number.strip().upper()
    if not validate_gst(gst_number):
        return jsonify({"error": "Invalid GSTIN format. Must be 15 characters (e.g. 22AAAAA0000A1Z5)"}), 400

    # 2. Duplicate Check within this organization
    org_id = current_user.organization_id
    duplicate_warning = None

    existing_customer = Customer.query.filter_by(
        organization_id=org_id, gst_number=gst_number, is_deleted=False
    ).first()
    if existing_customer:
        duplicate_warning = {
            "id": existing_customer.id,
            "type": "customer",
            "message": f"GST number already registered to Customer: {existing_customer.name}"
        }
    else:
        existing_lead = Lead.query.filter_by(
            organization_id=org_id, gst_number=gst_number, is_deleted=False
        ).first()
        if existing_lead:
            duplicate_warning = {
                "id": existing_lead.id,
                "type": "lead",
                "message": f"GST number already exists in Lead: {existing_lead.name}"
            }

    # 3. Load API credentials
    api_key = os.getenv("GST_CLIENT_ID")
    api_secret = os.getenv("GST_CLIENT_SECRET")

    if not api_key or not api_secret:
        return jsonify({"error": "GST API credentials not configured. Add GST_CLIENT_ID and GST_CLIENT_SECRET to .env"}), 500

    try:
        # Step A: Authenticate with Sandbox
        token, auth_error = get_sandbox_token(api_key, api_secret)
        if not token:
            return jsonify({"error": auth_error or "Failed to authenticate with GST API"}), 401

        # Step B: Search GSTIN
        # Correct endpoint per Sandbox docs: POST /gst/compliance/public/gstin/search
        search_headers = {
            "Authorization": token,  # Sandbox uses raw token, NOT "Bearer <token>"
            "x-api-key": api_key,
            "x-api-version": "1.0",
            "Content-Type": "application/json",
            "accept": "application/json"
        }
        search_payload = {"gstin": gst_number}

        response = requests.post(
            "https://api.sandbox.co.in/gst/compliance/public/gstin/search",
            json=search_payload,
            headers=search_headers,
            timeout=15
        )

        # If the primary endpoint fails, try the alternate endpoint
        if response.status_code in [404, 405, 403]:
            response = requests.get(
                f"https://api.sandbox.co.in/gsp/public/gstin/{gst_number}",
                headers=search_headers,
                timeout=15
            )

        # Handle API errors
        if response.status_code != 200:
            try:
                error_data = response.json()
                msg = (
                    error_data.get("message")
                    or error_data.get("error", {}).get("message", "")
                    or error_data.get("error_description", "")
                )
                if response.status_code == 401:
                    return jsonify({"error": f"GST API authentication expired or invalid. {msg}"}), 401
                elif response.status_code == 404:
                    return jsonify({"error": f"GSTIN {gst_number} not found on GST portal."}), 404
                elif response.status_code == 429:
                    return jsonify({"error": "GST API rate limit exceeded. Please wait and try again."}), 429
                else:
                    return jsonify({"error": f"GST API Error ({response.status_code}): {msg or 'Unknown error'}"}), response.status_code
            except ValueError:
                return jsonify({"error": f"GST API returned status {response.status_code}. Ensure 'GSTIN Public Search' is enabled in your Sandbox dashboard."}), response.status_code

        # Step C: Parse the response
        res_data = response.json()

        # Sandbox wraps GSTIN fields inside data.data (double nested)
        # Structure: {"code": 200, "data": {"data": {fields...}, "status_cd": "1"}}
        outer_data = res_data.get("data", res_data)
        if isinstance(outer_data, dict):
            data = outer_data.get("data", outer_data)
        elif isinstance(outer_data, list) and len(outer_data) > 0:
            data = outer_data[0]
        else:
            data = {}
        
        if not isinstance(data, dict):
            data = {}

        # Extract legal name (try multiple field names for compatibility)
        lgnm = (
            data.get("lgnm")
            or data.get("legal_name")
            or data.get("legalName")
            or data.get("company_name")
            or ""
        )

        # Extract trade name
        trade_name = (
            data.get("tradeNam")
            or data.get("trade_name")
            or data.get("tradeName")
            or lgnm
        )

        # Address parsing — Sandbox nests address fields inside pradr.addr
        city = ""
        state = ""
        pincode = ""
        full_address = ""

        pradr = data.get("pradr") or data.get("principal_address") or {}
        if isinstance(pradr, dict):
            # Address details are inside the 'addr' sub-object
            addr = pradr.get("addr", pradr)
            if isinstance(addr, dict):
                city = addr.get("dst") or addr.get("city") or addr.get("loc") or ""
                state = addr.get("stcd") or addr.get("st") or addr.get("state") or ""
                pincode = str(addr.get("pncd") or addr.get("pincode") or "")

                # Build full address from parts
                addr_parts = []
                for key in ["bno", "bnm", "flno", "st", "loc", "dst"]:
                    val = addr.get(key)
                    if val and str(val).strip():
                        addr_parts.append(str(val).strip())
                full_address = ", ".join(addr_parts) if addr_parts else ""

            # Also check for state/nature at pradr level
            if not state:
                state = pradr.get("addr", {}).get("stcd", "") or pradr.get("ntr", "")
        elif isinstance(pradr, str):
            full_address = pradr

        # Fallback to top-level fields
        if not state:
            state = data.get("state") or ""
        if not city:
            city = data.get("city") or ""
        if not pincode:
            pincode = str(data.get("pincode") or "")

        # Business type and status
        business_type = (
            data.get("ctb")
            or data.get("constitution")
            or data.get("business_type")
            or ""
        )
        gst_status = (
            data.get("sts")
            or data.get("status")
            or data.get("gst_status")
            or ""
        )

        # Construct unified response
        gst_data = {
            "company": lgnm,
            "trade_name": trade_name,
            "address": full_address,
            "city": city,
            "state": state,
            "pincode": pincode,
            "business_type": business_type,
            "gst_status": gst_status,
            "duplicate_warning": duplicate_warning
        }

        return jsonify(gst_data), 200

    except requests.exceptions.Timeout:
        return jsonify({"error": "GST API request timed out. The government portal may be slow. Please try again."}), 504
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Cannot connect to GST API. Check your internet connection."}), 503
    except Exception as e:
        return jsonify({"error": f"System Error: {str(e)}"}), 500
