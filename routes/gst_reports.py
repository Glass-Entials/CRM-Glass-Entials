from flask import (
    Blueprint, render_template, request, jsonify, send_file, current_app
)
from flask_login import login_required, current_user
from model import QuotationSettings
import datetime, io

from services.gst_export_service import (
    build_gst_report, build_excel, monthly_range, fy_range
)

gst_bp = Blueprint("gst_reports", __name__, url_prefix="/accounts/gst-reports")


def _get_org_id():
    return current_user.organization_id


def _get_settings(org_id):
    s = QuotationSettings.query.filter_by(organization_id=org_id).first()
    if not s:
        s = QuotationSettings(organization_id=org_id)
    return s


def _parse_filters(form):
    report_type = form.get("report_type", "monthly")
    if report_type == "monthly":
        year = int(form.get("year", datetime.date.today().year))
        month = int(form.get("month", datetime.date.today().month))
        start, end = monthly_range(year, month)
        label = f"{datetime.date(year, month, 1).strftime('%B %Y')}"
    else:
        fy_str = form.get("fy_year", str(datetime.date.today().year))
        fy_start = int(fy_str)
        start, end = fy_range(fy_start)
        label = f"FY {fy_start}-{str(fy_start + 1)[2:]}"
    return start, end, label


@gst_bp.route("/", methods=["GET"])
@login_required
def gst_report_page():
    today = datetime.date.today()
    years = list(range(today.year - 4, today.year + 2))
    months = [
        (1, "January"), (2, "February"), (3, "March"), (4, "April"),
        (5, "May"), (6, "June"), (7, "July"), (8, "August"),
        (9, "September"), (10, "October"), (11, "November"), (12, "December"),
    ]
    fy_years = [(y, f"FY {y}-{str(y + 1)[2:]}") for y in range(today.year - 4, today.year + 2)]
    return render_template(
        "accounts/gst_report.html",
        years=years,
        months=months,
        fy_years=fy_years,
        current_year=today.year,
        current_month=today.month,
    )


@gst_bp.route("/generate", methods=["POST"])
@login_required
def generate_report():
    try:
        org_id = _get_org_id()
        start, end, label = _parse_filters(request.form)
        report = build_gst_report(org_id, start, end)
        report["period_label"] = label
        return jsonify({"success": True, "report": report})
    except Exception as e:
        current_app.logger.exception("GST report generation failed")
        return jsonify({"success": False, "error": str(e)}), 500


@gst_bp.route("/export", methods=["POST"])
@login_required
def export_excel():
    try:
        org_id = _get_org_id()
        start, end, label = _parse_filters(request.form)
        settings = _get_settings(org_id)
        report = build_gst_report(org_id, start, end)
        excel_bytes = build_excel(report, settings, label)
        filename = f"GSTR1_{label.replace(' ', '_')}.xlsx"
        return send_file(
            io.BytesIO(excel_bytes),
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        current_app.logger.exception("GST Excel export failed")
        return jsonify({"success": False, "error": str(e)}), 500
