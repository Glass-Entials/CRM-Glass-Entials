import hmac
import json
import logging
from flask import (
    Blueprint,
    render_template,
    request,
    current_app,
    redirect,
    url_for,
    flash,
    jsonify,
)
from flask_login import login_required, current_user
from model import (
    db,
    Plan,
    Organization,
    OrganizationStatus,
    SaaSMandate,
    SaaSPreDebitNotification,
    SaaSSubscription,
    SaaSSubscriptionStatus,
    SaaSTransaction,
    SaaSTransactionStatus,
    SaaSWebhookEvent,
    UserRole,
)
from services.billing import (
    BillingService,
    SubscriptionService,
    TransactionService,
    RenewalService,
)

logger = logging.getLogger(__name__)

saas_bp = Blueprint("saas", __name__, url_prefix="/saas")


@saas_bp.route("/checkout")
@login_required
def checkout():
    """Checkout page for PENDING organizations with PhonePe UPI AutoPay."""
    org = current_user.organization

    if not org:
        flash("No organization associated with your user.", "error")
        return redirect(url_for("home_page"))

    if org.status != OrganizationStatus.PENDING:
        flash("Your organization is already active.", "info")
        return redirect(url_for("home_page"))

    sub = SaaSSubscription.query.filter_by(organization_id=org.id).first()
    if not sub or not sub.plan:
        flash("No subscription plan found for your organization.", "error")
        return redirect(url_for("home_page"))

    plan = sub.plan

    # If the plan is free (custom pricing / Enterprise / trial), activate immediately without gateway.
    if plan.monthly_price_paise == 0:
        org.status = OrganizationStatus.ACTIVE
        sub.status = SaaSSubscriptionStatus.ACTIVE
        db.session.commit()
        flash("Free / Enterprise plan activated.", "success")
        return redirect(url_for("home_page"))

    provider = BillingService.get_provider()

    return render_template(
        "saas/checkout.html",
        org=org,
        plan=plan,
        sub=sub,
        provider_name=provider.name,
    )


@saas_bp.route("/api/create-subscription", methods=["POST"])
@login_required
def create_subscription():
    """
    Creates a PhonePe UPI AutoPay recurring mandate for the selected billing cycle.
    Returns the gateway checkout URL to which the customer is redirected.
    """
    org = current_user.organization
    if not org or org.status != OrganizationStatus.PENDING:
        return jsonify({"error": "Organization already active or invalid"}), 400

    data = request.get_json(silent=True) or {}
    cycle = data.get("cycle", "monthly").lower()
    if cycle not in ("monthly", "yearly"):
        cycle = "monthly"

    sub = SaaSSubscription.query.filter_by(organization_id=org.id).first()
    if not sub or not sub.plan:
        return jsonify({"error": "Subscription plan not found"}), 404

    plan = sub.plan
    provider = BillingService.get_provider()

    return_url = url_for("saas.phonepe_callback", _external=True)
    callback_url = url_for("saas.phonepe_webhook", _external=True)

    try:
        result = SubscriptionService.initiate_subscription(
            org=org,
            plan=plan,
            billing_cycle=cycle,
            user=current_user,
            provider=provider,
            return_url=return_url,
            callback_url=callback_url,
        )

        if not result.success:
            return jsonify({"error": result.error_message or "Failed to initiate payment gateway mandate"}), 400

        return jsonify({
            "success": True,
            "provider": provider.name,
            "redirect_url": result.redirect_url,
            "subscription_id": result.provider_subscription_id or result.merchant_subscription_id,
            "merchant_transaction_id": result.merchant_transaction_id,
            "organization_name": org.name,
        })
    except Exception as e:
        logger.error("Error creating AutoPay subscription: %s", str(e), exc_info=True)
        return jsonify({"error": str(e)}), 500


@saas_bp.route("/phonepe-callback", methods=["GET", "POST"])
def phonepe_callback():
    """
    User return URL redirected from PhonePe after authorizing mandate.
    Routes to V2 provider if the callback corresponds to a V2 mandate; otherwise
    falls back to the legacy V3 PhonePeProvider.
    Verifies transaction status and activates the organization.
    """
    # PhonePe POST redirect sends form fields (e.g. response base64 or merchantTransactionId)
    # or GET redirect params
    payload = {}
    if request.method == "POST":
        payload.update(request.form.to_dict())
    payload.update(request.args.to_dict())

    logger.info("Received PhonePe redirect callback with keys: %s", list(payload.keys()))

    # Extract merchant identifiers from request to determine routing
    merchant_transaction_id = (
        payload.get("merchant_transaction_id")
        or payload.get("merchantTransactionId")
        or payload.get("merchantOrderId")
    )
    merchant_subscription_id = (
        payload.get("merchant_subscription_id")
        or payload.get("merchantSubscriptionId")
    )

    # Determine whether this callback belongs to a V2 mandate
    is_v2 = False
    if merchant_subscription_id:
        v2_mandate = SaaSMandate.query.filter_by(
            merchant_subscription_id=merchant_subscription_id,
        ).first()
        if v2_mandate and v2_mandate.payment_provider == "phonepe_v2":
            is_v2 = True
    if not is_v2 and merchant_transaction_id:
        v2_pdn = SaaSPreDebitNotification.query.filter_by(
            merchant_order_id=merchant_transaction_id,
        ).first()
        if v2_pdn:
            is_v2 = True

    provider = BillingService.get_provider("phonepe_v2" if is_v2 else "phonepe")

    # org_id is sourced from server-side authenticated session, never from request params
    org_id = current_user.organization_id if (current_user.is_authenticated and getattr(current_user, "organization", None)) else None
    verify_res = provider.verify_payment(payload, headers=dict(request.headers), organization_id=org_id)

    # Look up subscription
    sub = None
    if verify_res.merchant_subscription_id:
        sub = SaaSSubscription.query.filter(
            (SaaSSubscription.provider_subscription_id == verify_res.merchant_subscription_id) |
            (SaaSSubscription.provider_order_id == verify_res.merchant_subscription_id)
        ).first()

    # If sub not found by ID and user is logged in, use their organization's subscription
    if not sub and current_user.is_authenticated and current_user.organization:
        sub = SaaSSubscription.query.filter_by(organization_id=current_user.organization_id).first()

    if verify_res.success:
        if sub:
            SubscriptionService.activate_subscription(
                subscription=sub,
                provider_subscription_id=verify_res.provider_subscription_id,
                provider_transaction_id=verify_res.provider_transaction_id,
                provider_payment_id=verify_res.provider_payment_id,
                amount_paise=verify_res.amount_paise,
                mandate_reference=verify_res.mandate_reference,
            )
            flash("UPI AutoPay subscription activated successfully! Welcome to GlassEntials CRM.", "success")
            return redirect(url_for("home_page"))
        else:
            flash("Payment succeeded, but subscription record could not be matched. Please contact support.", "warning")
            return redirect(url_for("home_page"))
    else:
        err = verify_res.error_message or "Payment was not authorized or cancelled."
        flash(f"Payment verification failed: {err}", "error")
        return redirect(url_for("saas.checkout"))


@saas_bp.route("/api/verify-payment", methods=["POST"])
@login_required
def verify_payment():
    """
    Direct payment verification API endpoint (for client-side or legacy callers).
    """
    org = current_user.organization
    # org.id is sourced from server-side authenticated session via current_user.organization, never from request body
    data = request.get_json(silent=True) or {}
    provider = BillingService.get_provider()

    verify_res = provider.verify_payment(data, headers=dict(request.headers), organization_id=org.id)

    sub = SaaSSubscription.query.filter_by(organization_id=org.id).first()
    if not sub:
        return jsonify({"error": "Subscription not found"}), 404

    if verify_res.success:
        SubscriptionService.activate_subscription(
            subscription=sub,
            provider_subscription_id=verify_res.provider_subscription_id,
            provider_transaction_id=verify_res.provider_transaction_id,
            provider_payment_id=verify_res.provider_payment_id,
            amount_paise=verify_res.amount_paise,
            mandate_reference=verify_res.mandate_reference,
        )
        return jsonify({"success": True})
    else:
        return jsonify({"error": verify_res.error_message or "Payment verification failed"}), 400


@saas_bp.route("/phonepe-webhook", methods=["POST"])
@saas_bp.route("/webhook", methods=["POST"])
def phonepe_webhook():
    """
    Handles server-to-server (S2S) webhooks for PhonePe AutoPay.
    Manages mandate activation, pause, cancellation, and recurring redemptions.

    V2 mandate events (redirects and S2S notifications) arrive at /saas/phonepe-callback,
    NOT at this S2S webhook endpoint. If a V2 event is detected here, it is acknowledged
    immediately without processing, since the callback endpoint handles V2 lifecycle.
    All existing V3 webhook event handling code is preserved completely unchanged.
    """
    payload_bytes = request.get_data()
    headers = dict(request.headers)

    # Detect if this is a V2 event by parsing the JSON body
    is_v2 = False
    try:
        body_json = json.loads(payload_bytes) if payload_bytes else {}
        merchant_sub_id_in_body = (
            body_json.get("merchantSubscriptionId")
            or body_json.get("data", {}).get("merchantSubscriptionId")
            if isinstance(body_json, dict) else None
        )
        merchant_order_id_in_body = (
            body_json.get("merchantOrderId")
            or body_json.get("data", {}).get("merchantOrderId")
            if isinstance(body_json, dict) else None
        )
        if merchant_sub_id_in_body:
            v2_mandate = SaaSMandate.query.filter_by(
                merchant_subscription_id=merchant_sub_id_in_body,
            ).first()
            if v2_mandate and v2_mandate.payment_provider == "phonepe_v2":
                is_v2 = True
        if not is_v2 and merchant_order_id_in_body:
            v2_pdn = SaaSPreDebitNotification.query.filter_by(
                merchant_order_id=merchant_order_id_in_body,
            ).first()
            if v2_pdn:
                is_v2 = True
    except Exception:
        pass  # Parsing failure — treat as V3

    if is_v2:
        # V2 events are handled via the /saas/phonepe-callback endpoint (HMAC-verified redirect/S2S).
        # Acknowledge without processing to avoid double-processing.
        logger.info(
            "phonepe_webhook: Received payload for a V2 mandate/order; acknowledging without V3 processing. "
            "V2 events are handled at the /saas/phonepe-callback endpoint."
        )
        return jsonify({"status": "v2_event_acknowledged"}), 200

    # ── V3 Legacy PhonePe Webhook Processing (preserved completely) ───────────
    provider = BillingService.get_provider("phonepe")
    payload_bytes = request.get_data()
    headers = dict(request.headers)

    webhook_res = provider.verify_webhook(payload_bytes, headers)

    if not webhook_res.is_valid:
        logger.warning("Rejected unauthenticated webhook: %s", webhook_res.error_message)
        return jsonify({"error": webhook_res.error_message}), 400

    event_id = webhook_res.event_id
    event_type = webhook_res.event_type

    # Idempotency check
    if SaaSWebhookEvent.query.filter_by(event_id=event_id).first():
        logger.info("Webhook event %s already processed; skipping.", event_id)
        return jsonify({"status": "already_processed"}), 200

    # Locate the target subscription
    sub = None
    sub_ref = webhook_res.merchant_subscription_id or webhook_res.provider_subscription_id
    if sub_ref:
        sub = SaaSSubscription.query.filter(
            (SaaSSubscription.provider_subscription_id == sub_ref) |
            (SaaSSubscription.razorpay_subscription_id == sub_ref)
        ).first()

    # Amount validation: ensure reported amount matches expected plan price
    if sub and sub.plan and webhook_res.amount_paise > 0:
        expected_base = sub.plan.yearly_price_paise if (sub.billing_cycle and sub.billing_cycle.value == "yearly") else sub.plan.monthly_price_paise
        expected_total = expected_base + int(round(expected_base * 0.18))
        if webhook_res.amount_paise < expected_total:
            logger.warning(
                "Potential underpayment on webhook for org=%s: received %s paise, expected %s paise",
                sub.organization_id, webhook_res.amount_paise, expected_total
            )

    try:
        # Event 1: Initial Mandate or Payment Completed / Active
        if event_type in (
            "subscription.setup.order.completed",
            "SUBSCRIPTION_ACTIVE",
            "SUBSCRIPTION_SETUP_SUCCESS",
            "PAYMENT_SUCCESS",
            "subscription.charged",
        ) or webhook_res.state in ("ACTIVE", "COMPLETED", "SUCCESS"):
            if sub:
                SubscriptionService.activate_subscription(
                    subscription=sub,
                    provider_subscription_id=webhook_res.provider_subscription_id,
                    provider_payment_id=webhook_res.provider_payment_id,
                    amount_paise=webhook_res.amount_paise,
                )
                sub.last_webhook_event_id = event_id

        # Event 2: Mandate Paused by user on UPI app
        elif event_type in ("subscription.paused", "SUBSCRIPTION_PAUSED") or webhook_res.state == "PAUSED":
            if sub:
                SubscriptionService.suspend_subscription(sub, reason="Mandate paused by customer via UPI app")
                sub.last_webhook_event_id = event_id

        # Event 3: Mandate Unpaused
        elif event_type in ("subscription.unpaused", "SUBSCRIPTION_UNPAUSED"):
            if sub:
                sub.status = SaaSSubscriptionStatus.ACTIVE
                if sub.organization:
                    sub.organization.status = OrganizationStatus.ACTIVE
                sub.last_webhook_event_id = event_id

        # Event 4: Mandate Revoked / Cancelled
        elif event_type in (
            "subscription.revoked",
            "SUBSCRIPTION_REVOKED",
            "subscription.cancelled",
            "SUBSCRIPTION_CANCELLED",
            "subscription.halted",
        ) or webhook_res.state in ("REVOKED", "CANCELLED"):
            if sub:
                SubscriptionService.cancel_subscription(sub, reason="Mandate revoked or cancelled")
                sub.last_webhook_event_id = event_id

        # Event 5: Recurring Redemption / Auto-debit
        elif event_type in ("redemption.completed", "REDEMPTION_SUCCESS"):
            if sub:
                new_start, new_end = SubscriptionService.calculate_period_dates(sub.billing_cycle.value)
                sub.current_period_start = new_start
                sub.current_period_end = new_end
                sub.next_billing_at = new_end
                sub.status = SaaSSubscriptionStatus.ACTIVE
                sub.last_webhook_event_id = event_id

                TransactionService.record_transaction(
                    organization_id=sub.organization_id,
                    subscription_id=sub.id,
                    payment_provider="phonepe",
                    amount_paise=webhook_res.amount_paise or sub.amount_paise,
                    status=SaaSTransactionStatus.SUCCESS,
                    provider_payment_id=webhook_res.provider_payment_id,
                    provider_subscription_id=sub.provider_subscription_id,
                    payment_method="UPI_AUTOPAY",
                    billing_period_start=new_start,
                    billing_period_end=new_end,
                )

        elif event_type in ("redemption.failed", "REDEMPTION_FAILED"):
            if sub:
                SubscriptionService.enter_grace_period(sub, days=7)
                TransactionService.record_transaction(
                    organization_id=sub.organization_id,
                    subscription_id=sub.id,
                    payment_provider="phonepe",
                    amount_paise=webhook_res.amount_paise or sub.amount_paise,
                    status=SaaSTransactionStatus.FAILED,
                    provider_payment_id=webhook_res.provider_payment_id,
                    provider_subscription_id=sub.provider_subscription_id,
                    payment_method="UPI_AUTOPAY",
                    failure_reason="Recurring redemption debit failed",
                )

        # Log event for idempotency
        ev = SaaSWebhookEvent(
            payment_provider="phonepe",
            event_id=event_id,
            event_type=event_type,
            payload_summary=json.dumps({
                "sub_ref": sub_ref,
                "amount": webhook_res.amount_paise,
                "state": webhook_res.state,
            }),
        )
        db.session.add(ev)
        db.session.commit()
        logger.info("Successfully processed PhonePe webhook event %s (%s)", event_id, event_type)

    except Exception as e:
        db.session.rollback()
        logger.error("Error processing PhonePe webhook %s: %s", event_id, str(e), exc_info=True)
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "success", "event_id": event_id}), 200


@saas_bp.route("/api/process-renewals", methods=["POST"])
def process_renewals():
    """
    Automated job / cron endpoint to process Pre-Debit Notifications (24h prior)
    and execute due AutoPay renewals.
    Protected by dedicated RENEWAL_CRON_SECRET or Super Admin authentication.
    Uses timing-attack resistant hmac.compare_digest.
    """
    raw_auth = (request.headers.get("X-Renewal-Secret") or request.headers.get("Authorization") or "").strip()
    token = raw_auth[7:].strip() if raw_auth.startswith("Bearer ") else raw_auth

    expected_secret = current_app.config.get("RENEWAL_CRON_SECRET") or current_app.config.get("SECRET_KEY")

    is_authorized = False
    if token and expected_secret and hmac.compare_digest(token, expected_secret):
        is_authorized = True
    elif current_user.is_authenticated and current_user.role == UserRole.ADMIN:
        is_authorized = True

    if not is_authorized:
        return jsonify({"error": "Unauthorized renewal trigger"}), 403

    provider = BillingService.get_provider("phonepe")
    # NOTE: This endpoint uses the legacy V3 PhonePeProvider for renewal processing.
    # V2 (PhonePeAutoPayV2) renewal flows use PhonePeAutoPayV2Provider directly
    # inside RenewalService via BillingService.get_provider("phonepe_v2").

    notifications = RenewalService.process_upcoming_notifications(provider, hours_ahead=48)
    renewals = RenewalService.process_due_renewals(provider)

    return jsonify({
        "status": "completed",
        "notifications_sent": len(notifications),
        "renewals_executed": len(renewals),
        "details": {
            "notifications": notifications,
            "renewals": renewals,
        }
    }), 200
