import hmac
import hashlib
import razorpay
from flask import Blueprint, render_template, request, current_app, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from model import db, Plan, Organization, OrganizationStatus, SaaSSubscription, SaaSSubscriptionStatus, SaaSTransaction, SaaSTransactionStatus

saas_bp = Blueprint("saas", __name__, url_prefix="/saas")

def get_razorpay_client():
    key_id = current_app.config.get("RAZORPAY_KEY_ID")
    key_secret = current_app.config.get("RAZORPAY_KEY_SECRET")
    if not key_id or not key_secret:
        raise ValueError("Razorpay configuration missing")
    return razorpay.Client(auth=(key_id, key_secret))

@saas_bp.route("/checkout")
@login_required
def checkout():
    """Checkout page for PENDING organizations."""
    org = current_user.organization
    
    # Check if user is authorized to manage billing (admin/owner)
    # Simple check for now: if org is pending, someone needs to pay for it.
    
    if org.status != OrganizationStatus.PENDING:
        flash("Your organization is already active.", "info")
        return redirect(url_for("home_page"))

    sub = SaaSSubscription.query.filter_by(organization_id=org.id).first()
    if not sub or not sub.plan:
        flash("No subscription plan found for your organization.", "error")
        return redirect(url_for("home_page"))

    plan = sub.plan
    key_id = current_app.config.get("RAZORPAY_KEY_ID")

    # If the plan is free (custom pricing / Enterprise), we don't need a checkout.
    if plan.monthly_price_paise == 0:
        org.status = OrganizationStatus.ACTIVE
        sub.status = SaaSSubscriptionStatus.ACTIVE
        db.session.commit()
        flash("Enterprise plan activated.", "success")
        return redirect(url_for("home_page"))

    return render_template("saas/checkout.html", org=org, plan=plan, sub=sub, key_id=key_id)

@saas_bp.route("/api/create-subscription", methods=["POST"])
@login_required
def create_subscription():
    """Create a Razorpay subscription."""
    org = current_user.organization
    if org.status != OrganizationStatus.PENDING:
        return jsonify({"error": "Organization already active"}), 400
        
    data = request.json
    cycle = data.get("cycle", "monthly")
    
    sub = SaaSSubscription.query.filter_by(organization_id=org.id).first()
    if not sub or not sub.plan:
        return jsonify({"error": "Plan not found"}), 404
        
    plan = sub.plan
    client = get_razorpay_client()
    
    # Get the correct razorpay plan ID
    razorpay_plan_id = plan.razorpay_yearly_plan_id if cycle == "yearly" else plan.razorpay_monthly_plan_id
    if not razorpay_plan_id:
        # Fallback: if no plan ID is set in DB, this means Razorpay plans aren't configured.
        # In a real scenario, the SuperAdmin configures these. 
        # For development/demo, we can't create a subscription without a valid plan ID.
        return jsonify({"error": f"Razorpay {cycle} plan ID not configured for this plan."}), 400

    try:
        # Create subscription in Razorpay
        rp_sub = client.subscription.create({
            "plan_id": razorpay_plan_id,
            "customer_notify": 1,
            "total_count": 120, # 10 years
        })
        
        # Update local subscription
        sub.razorpay_subscription_id = rp_sub['id']
        sub.razorpay_plan_id = razorpay_plan_id
        sub.billing_cycle = cycle
        db.session.commit()
        
        return jsonify({
            "subscription_id": rp_sub['id'],
            "organization_name": org.name,
            "user_name": current_user.username,
            "user_email": current_user.email,
            "user_phone": current_user.phone_number
        })
    except Exception as e:
        current_app.logger.error(f"Error creating subscription: {str(e)}")
        return jsonify({"error": str(e)}), 500

@saas_bp.route("/api/verify-payment", methods=["POST"])
@login_required
def verify_payment():
    """Verify Razorpay payment signature."""
    org = current_user.organization
    data = request.json
    
    payment_id = data.get("razorpay_payment_id")
    subscription_id = data.get("razorpay_subscription_id")
    signature = data.get("razorpay_signature")
    
    if not all([payment_id, subscription_id, signature]):
        return jsonify({"error": "Missing payment parameters"}), 400
        
    client = get_razorpay_client()
    
    try:
        # Verify signature
        client.utility.verify_subscription_payment_signature({
            'razorpay_subscription_id': subscription_id,
            'razorpay_payment_id': payment_id,
            'razorpay_signature': signature
        })
        
        # Mark as active
        org.status = OrganizationStatus.ACTIVE
        sub = SaaSSubscription.query.filter_by(organization_id=org.id).first()
        if sub:
            sub.status = SaaSSubscriptionStatus.ACTIVE
            sub.razorpay_subscription_id = subscription_id
            
        # Record transaction
        tx = SaaSTransaction(
            organization_id=org.id,
            subscription_id=sub.id if sub else None,
            razorpay_payment_id=payment_id,
            razorpay_subscription_id=subscription_id,
            status=SaaSTransactionStatus.SUCCESS
        )
        db.session.add(tx)
        db.session.commit()
        
        return jsonify({"success": True})
        
    except razorpay.errors.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400
    except Exception as e:
        current_app.logger.error(f"Error verifying payment: {str(e)}")
        return jsonify({"error": str(e)}), 500

@saas_bp.route("/webhook", methods=["POST"])
def razorpay_webhook():
    """Handle Razorpay webhooks (e.g. subscription charged, halted)."""
    webhook_secret = current_app.config.get("RAZORPAY_WEBHOOK_SECRET")
    if not webhook_secret:
        return "Webhook secret not configured", 500
        
    payload = request.get_data()
    signature = request.headers.get("X-Razorpay-Signature")
    
    if not signature:
        return "Missing signature", 400
        
    client = get_razorpay_client()
    try:
        client.utility.verify_webhook_signature(payload.decode('utf-8'), signature, webhook_secret)
    except razorpay.errors.SignatureVerificationError:
        return "Invalid signature", 400
        
    data = request.json
    event_id = data.get("x-razorpay-event-id") or data.get("id")  # Fallback
    event_type = data.get("event")
    
    # Idempotency check
    from model import SaaSWebhookEvent
    if SaaSWebhookEvent.query.filter_by(event_id=event_id).first():
        return "Event already processed", 200
        
    # Process event
    try:
        if event_type == "subscription.charged":
            sub_id = data['payload']['subscription']['entity']['id']
            payment_id = data['payload']['payment']['entity']['id']
            amount = data['payload']['payment']['entity']['amount']
            
            sub = SaaSSubscription.query.filter_by(razorpay_subscription_id=sub_id).first()
            if sub:
                sub.status = SaaSSubscriptionStatus.ACTIVE
                sub.last_webhook_event_id = event_id
                
                # Record transaction
                if not SaaSTransaction.query.filter_by(razorpay_payment_id=payment_id).first():
                    tx = SaaSTransaction(
                        organization_id=sub.organization_id,
                        subscription_id=sub.id,
                        razorpay_payment_id=payment_id,
                        razorpay_subscription_id=sub_id,
                        razorpay_event_id=event_id,
                        amount_paise=amount,
                        status=SaaSTransactionStatus.SUCCESS
                    )
                    db.session.add(tx)
                    
                sub.organization.status = OrganizationStatus.ACTIVE

        elif event_type == "subscription.halted":
            sub_id = data['payload']['subscription']['entity']['id']
            sub = SaaSSubscription.query.filter_by(razorpay_subscription_id=sub_id).first()
            if sub:
                sub.status = SaaSSubscriptionStatus.SUSPENDED
                sub.organization.status = OrganizationStatus.SUSPENDED
                sub.last_webhook_event_id = event_id
                
        elif event_type == "subscription.cancelled":
            sub_id = data['payload']['subscription']['entity']['id']
            sub = SaaSSubscription.query.filter_by(razorpay_subscription_id=sub_id).first()
            if sub:
                sub.status = SaaSSubscriptionStatus.CANCELLED
                sub.organization.status = OrganizationStatus.CANCELLED
                sub.last_webhook_event_id = event_id

        # Log event for idempotency
        import json
        ev = SaaSWebhookEvent(
            event_id=event_id,
            event_type=event_type,
            payload_summary=json.dumps({"sub_id": data.get('payload', {}).get('subscription', {}).get('entity', {}).get('id')})
        )
        db.session.add(ev)
        db.session.commit()
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Webhook processing error: {str(e)}")
        return str(e), 500

    return "OK", 200
