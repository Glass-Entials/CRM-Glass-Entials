import os
import json
from dotenv import load_dotenv
import logging

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, ".env"))

def get_aws_secret(secret_name, region_name):
    import boto3
    from botocore.exceptions import ClientError
    session = boto3.session.Session()
    client = session.client(service_name='secretsmanager', region_name=region_name)
    try:
        get_secret_value_response = client.get_secret_value(SecretId=secret_name)
    except ClientError as e:
        logging.error(f"Failed to retrieve secret {secret_name}: {e}")
        raise e
    secret = get_secret_value_response['SecretString']
    return json.loads(secret)

class Config:
    use_aws_secrets = os.environ.get("USE_AWS_SECRETS", "false").lower() == "true"

    if use_aws_secrets:
        secret_name = os.environ.get("AWS_SECRET_NAME", "crm-glassentials/prod")
        region_name = os.environ.get("AWS_REGION", "ap-south-1")
        try:
            secrets = get_aws_secret(secret_name, region_name)
            SECRET_KEY = secrets.get("SECRET_KEY")
            SQLALCHEMY_DATABASE_URI = secrets.get("DATABASE_URL")
            GST_CLIENT_ID = secrets.get("GST_CLIENT_ID")
            GST_CLIENT_SECRET = secrets.get("GST_CLIENT_SECRET")
            RAZORPAY_KEY_ID = secrets.get("RAZORPAY_KEY_ID")
            RAZORPAY_KEY_SECRET = secrets.get("RAZORPAY_KEY_SECRET")
            # PhonePe Payment Gateway / UPI AutoPay
            PHONEPE_MERCHANT_ID = secrets.get("PHONEPE_MERCHANT_ID") or os.environ.get("PHONEPE_MERCHANT_ID", "")
            PHONEPE_SALT_KEY = secrets.get("PHONEPE_SALT_KEY") or os.environ.get("PHONEPE_SALT_KEY", "099eb0cd-0252-4e2a-9123-046648858427")
            PHONEPE_SALT_INDEX = secrets.get("PHONEPE_SALT_INDEX") or os.environ.get("PHONEPE_SALT_INDEX", "1")
            PHONEPE_ENV = secrets.get("PHONEPE_ENV") or os.environ.get("PHONEPE_ENV", "UAT")
            PHONEPE_WEBHOOK_USERNAME = secrets.get("PHONEPE_WEBHOOK_USERNAME") or os.environ.get("PHONEPE_WEBHOOK_USERNAME", "")
            PHONEPE_WEBHOOK_PASSWORD = secrets.get("PHONEPE_WEBHOOK_PASSWORD") or os.environ.get("PHONEPE_WEBHOOK_PASSWORD", "")
            PHONEPE_WEBHOOK_SECRET = secrets.get("PHONEPE_WEBHOOK_SECRET") or os.environ.get("PHONEPE_WEBHOOK_SECRET", "")
            PHONEPE_BASE_URL = secrets.get("PHONEPE_BASE_URL") or os.environ.get("PHONEPE_BASE_URL", "")
            BILLING_DEFAULT_PROVIDER = secrets.get("BILLING_DEFAULT_PROVIDER") or os.environ.get("BILLING_DEFAULT_PROVIDER", "phonepe")
            RENEWAL_CRON_SECRET = secrets.get("RENEWAL_CRON_SECRET") or os.environ.get("RENEWAL_CRON_SECRET", "")
            # PhonePe AutoPay V2 OAuth credentials (separate from legacy Salt Key / Merchant ID)
            PHONEPE_CLIENT_ID      = secrets.get("PHONEPE_CLIENT_ID") or os.environ.get("PHONEPE_CLIENT_ID", "")
            PHONEPE_CLIENT_SECRET  = secrets.get("PHONEPE_CLIENT_SECRET") or os.environ.get("PHONEPE_CLIENT_SECRET", "")
            PHONEPE_CLIENT_VERSION = secrets.get("PHONEPE_CLIENT_VERSION") or os.environ.get("PHONEPE_CLIENT_VERSION", "1")
            _is_prod_env = os.environ.get("FLASK_ENV", "").strip().lower() == "production"
            _default_v3 = "false" if _is_prod_env else "true"
            ENABLE_PHONEPE_V3_WEBHOOK = str(secrets.get("ENABLE_PHONEPE_V3_WEBHOOK") or os.environ.get("ENABLE_PHONEPE_V3_WEBHOOK", _default_v3)).strip().lower() == "true"
            ALLOW_LEGACY_PHONEPE_V3 = str(secrets.get("ALLOW_LEGACY_PHONEPE_V3") or os.environ.get("ALLOW_LEGACY_PHONEPE_V3", "false")).strip().lower() == "true"
        except Exception as e:
            logging.error("Could not load AWS secrets, falling back to env")
            SECRET_KEY = os.environ.get("SECRET_KEY")
            SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
            GST_CLIENT_ID = os.environ.get("GST_CLIENT_ID")
            GST_CLIENT_SECRET = os.environ.get("GST_CLIENT_SECRET")
            RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
            RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")
            # PhonePe Payment Gateway / UPI AutoPay
            PHONEPE_MERCHANT_ID = os.environ.get("PHONEPE_MERCHANT_ID", "")
            PHONEPE_SALT_KEY = os.environ.get("PHONEPE_SALT_KEY", "099eb0cd-0252-4e2a-9123-046648858427")
            PHONEPE_SALT_INDEX = os.environ.get("PHONEPE_SALT_INDEX", "1")
            PHONEPE_ENV = os.environ.get("PHONEPE_ENV", "UAT")
            PHONEPE_WEBHOOK_USERNAME = os.environ.get("PHONEPE_WEBHOOK_USERNAME", "")
            PHONEPE_WEBHOOK_PASSWORD = os.environ.get("PHONEPE_WEBHOOK_PASSWORD", "")
            PHONEPE_WEBHOOK_SECRET = os.environ.get("PHONEPE_WEBHOOK_SECRET", "")
            PHONEPE_BASE_URL = os.environ.get("PHONEPE_BASE_URL", "")
            BILLING_DEFAULT_PROVIDER = os.environ.get("BILLING_DEFAULT_PROVIDER", "phonepe")
            RENEWAL_CRON_SECRET = os.environ.get("RENEWAL_CRON_SECRET", "")
            # PhonePe AutoPay V2 OAuth credentials (separate from legacy Salt Key / Merchant ID)
            PHONEPE_CLIENT_ID      = os.environ.get("PHONEPE_CLIENT_ID", "")
            PHONEPE_CLIENT_SECRET  = os.environ.get("PHONEPE_CLIENT_SECRET", "")
            PHONEPE_CLIENT_VERSION = os.environ.get("PHONEPE_CLIENT_VERSION", "1")
            _is_prod_env = os.environ.get("FLASK_ENV", "").strip().lower() == "production"
            _default_v3 = "false" if _is_prod_env else "true"
            ENABLE_PHONEPE_V3_WEBHOOK = os.environ.get("ENABLE_PHONEPE_V3_WEBHOOK", _default_v3).strip().lower() == "true"
            ALLOW_LEGACY_PHONEPE_V3 = os.environ.get("ALLOW_LEGACY_PHONEPE_V3", "false").strip().lower() == "true"
    else:
        SECRET_KEY = os.environ.get("SECRET_KEY")
        SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
        GST_CLIENT_ID = os.environ.get("GST_CLIENT_ID")
        GST_CLIENT_SECRET = os.environ.get("GST_CLIENT_SECRET")
        RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
        RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")
        # PhonePe Payment Gateway / UPI AutoPay
        PHONEPE_MERCHANT_ID = os.environ.get("PHONEPE_MERCHANT_ID", "")
        PHONEPE_SALT_KEY = os.environ.get("PHONEPE_SALT_KEY", "099eb0cd-0252-4e2a-9123-046648858427")
        PHONEPE_SALT_INDEX = os.environ.get("PHONEPE_SALT_INDEX", "1")
        PHONEPE_ENV = os.environ.get("PHONEPE_ENV", "UAT")
        PHONEPE_WEBHOOK_USERNAME = os.environ.get("PHONEPE_WEBHOOK_USERNAME", "")
        PHONEPE_WEBHOOK_PASSWORD = os.environ.get("PHONEPE_WEBHOOK_PASSWORD", "")
        PHONEPE_WEBHOOK_SECRET = os.environ.get("PHONEPE_WEBHOOK_SECRET", "")
        PHONEPE_BASE_URL = os.environ.get("PHONEPE_BASE_URL", "")
        BILLING_DEFAULT_PROVIDER = os.environ.get("BILLING_DEFAULT_PROVIDER", "phonepe")
        RENEWAL_CRON_SECRET = os.environ.get("RENEWAL_CRON_SECRET", "")
        # PhonePe AutoPay V2 OAuth credentials (separate from legacy Salt Key / Merchant ID)
        PHONEPE_CLIENT_ID      = os.environ.get("PHONEPE_CLIENT_ID", "")
        PHONEPE_CLIENT_SECRET  = os.environ.get("PHONEPE_CLIENT_SECRET", "")
        PHONEPE_CLIENT_VERSION = os.environ.get("PHONEPE_CLIENT_VERSION", "1")
        _is_prod_env = os.environ.get("FLASK_ENV", "").strip().lower() == "production"
        _default_v3 = "false" if _is_prod_env else "true"
        ENABLE_PHONEPE_V3_WEBHOOK = os.environ.get("ENABLE_PHONEPE_V3_WEBHOOK", _default_v3).strip().lower() == "true"
        ALLOW_LEGACY_PHONEPE_V3 = os.environ.get("ALLOW_LEGACY_PHONEPE_V3", "false").strip().lower() == "true"

    FLASK_ENV = os.environ.get("FLASK_ENV", "development")

    # Billing can be explicitly disabled while the CRM core remains available.
    # Default stays enabled outside production; production defaults to fail-closed.
    _billing_default = "false" if FLASK_ENV.strip().lower() == "production" else "true"
    BILLING_ENABLED = os.environ.get("BILLING_ENABLED", _billing_default).strip().lower() in ("true", "1", "yes")

    if not SECRET_KEY or SECRET_KEY == "secret123":
        raise RuntimeError("Set a strong SECRET_KEY in the environment before startup.")

    if not os.environ.get("DATABASE_URL") and not use_aws_secrets:
        raise RuntimeError("Set DATABASE_URL in the environment before startup.")

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Private storage directory outside public web root for business documents
    PRIVATE_UPLOAD_FOLDER = os.environ.get(
        "PRIVATE_UPLOAD_FOLDER",
        os.path.join(basedir, "var", "uploads")
    )
    # Default upload folder points to private storage directory
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", PRIVATE_UPLOAD_FOLDER)
    # Legacy upload folder preserved for backward-compatible fallback access to existing documents
    LEGACY_UPLOAD_FOLDER = os.path.join(basedir, "static", "uploads")
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 5 * 1024 * 1024))

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"
    PERMANENT_SESSION_LIFETIME = int(os.environ.get("PERMANENT_SESSION_LIFETIME", 28800))

    # CSRF token lifetime — set to match session (8 hours). Default is 3600s (1hr) which is too short.
    WTF_CSRF_TIME_LIMIT = int(os.environ.get("WTF_CSRF_TIME_LIMIT", 28800))


def is_production_mode(cfg=None) -> bool:
    """
    Determines whether the application is running in production mode.
    Strictly requires FLASK_ENV=production.
    Raises RuntimeError if ENV=production is supplied without FLASK_ENV=production
    to prevent configuration drift or bypassing HTTPS/Talisman enforcement.
    """
    def _get(key, default=""):
        if cfg is None:
            val = getattr(Config, key, None)
            if val is None:
                val = os.environ.get(key, default)
            return val
        if isinstance(cfg, dict):
            return cfg.get(key, default)
        val = getattr(cfg, key, None)
        if val is None and hasattr(cfg, "get"):
            val = cfg.get(key, default)
        if val is None:
            val = os.environ.get(key, default)
        return val

    flask_env = str(_get("FLASK_ENV", "")).strip().lower()
    legacy_env = str(_get("ENV", "")).strip().lower()

    if legacy_env == "production" and flask_env != "production":
        raise RuntimeError(
            "Production configuration error: FLASK_ENV must be explicitly set to 'production' "
            "(found ENV='production' but FLASK_ENV is not 'production')."
        )

    return flask_env == "production"


def validate_production_config(cfg=None, force: bool = False):
    """
    Validates production configuration invariants when production mode is active.
    Strictly requires FLASK_ENV=production.
    Raises RuntimeError if any required production setting is invalid or missing.
    Never logs or discloses secret values in error messages.
    """
    def _get(key, default=""):
        if cfg is None:
            val = getattr(Config, key, None)
            if val is None:
                val = os.environ.get(key, default)
            return val
        if isinstance(cfg, dict):
            return cfg.get(key, default)
        val = getattr(cfg, key, None)
        if val is None and hasattr(cfg, "get"):
            val = cfg.get(key, default)
        if val is None:
            val = os.environ.get(key, default)
        return val

    flask_env = str(_get("FLASK_ENV", "")).strip().lower()
    legacy_env = str(_get("ENV", "")).strip().lower()

    # Reject ENV=production without FLASK_ENV=production
    if legacy_env == "production" and flask_env != "production":
        raise RuntimeError(
            "Production configuration error: FLASK_ENV must be explicitly set to 'production' "
            "(found ENV='production' but FLASK_ENV is not 'production')."
        )

    is_prod = (flask_env == "production") or force

    if not is_prod:
        return

    # In production mode (or when forced), FLASK_ENV must be explicitly 'production'
    if flask_env != "production":
        raise RuntimeError(
            f"Production configuration error: FLASK_ENV must be explicitly set to 'production' in production mode (got '{flask_env}')."
        )

    billing_enabled_raw = _get("BILLING_ENABLED", True)
    if isinstance(billing_enabled_raw, str):
        billing_enabled = billing_enabled_raw.strip().lower() in ("true", "1", "yes")
    else:
        billing_enabled = bool(billing_enabled_raw)

    # Payment gateway credentials are mandatory only when billing is enabled.
    # With billing disabled, gateway routes are separately fail-closed.
    if billing_enabled:
        # 1. Billing Default Provider must be phonepe_v2
        provider = str(_get("BILLING_DEFAULT_PROVIDER", "")).strip().lower()
        if provider != "phonepe_v2":
            raise RuntimeError(
                f"Production configuration error: BILLING_DEFAULT_PROVIDER must be 'phonepe_v2' in production (got '{provider}')."
            )

        # 2. PhonePe Environment must be PRODUCTION
        phonepe_env = str(_get("PHONEPE_ENV", "")).strip().upper()
        if phonepe_env != "PRODUCTION":
            raise RuntimeError(
                f"Production configuration error: PHONEPE_ENV must be 'PRODUCTION' in production (got '{phonepe_env}')."
            )

        # 3. PhonePe Client ID and Secret must be non-empty
        client_id = str(_get("PHONEPE_CLIENT_ID", "")).strip()
        if not client_id:
            raise RuntimeError(
                "Production configuration error: PHONEPE_CLIENT_ID is required and cannot be empty in production."
            )

        client_secret = str(_get("PHONEPE_CLIENT_SECRET", "")).strip()
        if not client_secret:
            raise RuntimeError(
                "Production configuration error: PHONEPE_CLIENT_SECRET is required and cannot be empty in production."
            )

        # 4. PhonePe Client Version must be a positive integer
        client_version_raw = str(_get("PHONEPE_CLIENT_VERSION", "")).strip()
        if not client_version_raw:
            raise RuntimeError(
                "Production configuration error: PHONEPE_CLIENT_VERSION is required and cannot be empty in production."
            )
        try:
            client_version_int = int(client_version_raw)
            if client_version_int < 1:
                raise ValueError()
        except (ValueError, TypeError):
            raise RuntimeError(
                f"Production configuration error: PHONEPE_CLIENT_VERSION must be a positive integer (got '{client_version_raw}')."
            )

        # 5. Merchant ID validation:
        # Direct PhonePe AutoPay V2 merchants authenticate via OAuth (Client ID, Client Secret, Client Version).
        # Merchant ID is optional for direct V2 merchants and omitted from the V2 business dashboard.
        # If PHONEPE_MERCHANT_ID is provided (e.g. for partner/aggregator integration via X-MERCHANT-ID),
        # it must NOT be a test/sandbox merchant ID in production.
        merchant_id = str(_get("PHONEPE_MERCHANT_ID", "")).strip()
        if merchant_id and merchant_id.upper() in {"PGTESTPAYUAT", "UAT"}:
            raise RuntimeError(
                f"Production configuration error: PHONEPE_MERCHANT_ID cannot be test merchant '{merchant_id}' in production."
            )

        # 6. Webhook credentials must be non-empty
        webhook_user = str(_get("PHONEPE_WEBHOOK_USERNAME", "")).strip()
        if not webhook_user:
            raise RuntimeError(
                "Production configuration error: PHONEPE_WEBHOOK_USERNAME is required and cannot be empty in production."
            )

        webhook_pass = str(_get("PHONEPE_WEBHOOK_PASSWORD", "")).strip()
        if not webhook_pass:
            raise RuntimeError(
                "Production configuration error: PHONEPE_WEBHOOK_PASSWORD is required and cannot be empty in production."
            )

        # 7. Dedicated RENEWAL_CRON_SECRET must be non-empty and distinct from SECRET_KEY
        renewal_secret = str(_get("RENEWAL_CRON_SECRET", "")).strip()
        if not renewal_secret:
            raise RuntimeError(
                "Production configuration error: Dedicated RENEWAL_CRON_SECRET is required and cannot be empty in production."
            )

        secret_key = str(_get("SECRET_KEY", "")).strip()
        if secret_key and renewal_secret == secret_key:
            raise RuntimeError(
                "Production configuration error: RENEWAL_CRON_SECRET must not reuse SECRET_KEY."
            )

        # 8. Production V3 Webhook & Legacy Compatibility must fail closed unless explicitly authorized
        enable_v3_raw = _get("ENABLE_PHONEPE_V3_WEBHOOK", False)
        if isinstance(enable_v3_raw, str):
            enable_v3 = enable_v3_raw.strip().lower() in ("true", "1", "yes")
        else:
            enable_v3 = bool(enable_v3_raw)

        allow_legacy_v3_raw = _get("ALLOW_LEGACY_PHONEPE_V3", False)
        if isinstance(allow_legacy_v3_raw, str):
            allow_legacy_v3 = allow_legacy_v3_raw.strip().lower() in ("true", "1", "yes")
        else:
            allow_legacy_v3 = bool(allow_legacy_v3_raw)

        if enable_v3 and not allow_legacy_v3:
            raise RuntimeError(
                "Production configuration error: ENABLE_PHONEPE_V3_WEBHOOK cannot be enabled in production "
                "without explicit authorization (set ALLOW_LEGACY_PHONEPE_V3=true to authorize legacy V3 compatibility)."
            )

        # When legacy V3 is explicitly authorized in production, legacy V3 credentials (Merchant ID & Salt Key) are strictly required.
        if enable_v3 and allow_legacy_v3:
            if not merchant_id:
                raise RuntimeError(
                    "Production configuration error: PHONEPE_MERCHANT_ID is required in production when legacy PhonePe V3 is enabled."
                )
            salt_key = str(_get("PHONEPE_SALT_KEY", "")).strip()
            if not salt_key or salt_key == "099eb0cd-0252-4e2a-9123-046648858427":
                raise RuntimeError(
                    "Production configuration error: Valid production PHONEPE_SALT_KEY is required in production when legacy PhonePe V3 is enabled."
                )

    # 9. AWS Secrets validation if USE_AWS_SECRETS is active
    use_aws_raw = _get("USE_AWS_SECRETS", False)
    if isinstance(use_aws_raw, str):
        use_aws = use_aws_raw.strip().lower() in ("true", "1", "yes")
    else:
        use_aws = bool(use_aws_raw)

    if use_aws:
        aws_region = str(_get("AWS_REGION", "")).strip()
        aws_secret_name = str(_get("AWS_SECRET_NAME", "")).strip()
        if not aws_region:
            raise RuntimeError(
                "Production configuration error: AWS_REGION is required when USE_AWS_SECRETS=true."
            )
        if not aws_secret_name:
            raise RuntimeError(
                "Production configuration error: AWS_SECRET_NAME is required when USE_AWS_SECRETS=true."
            )

    # 10. Core SECRET_KEY validation
    secret_key = str(_get("SECRET_KEY", "")).strip()
    if not secret_key or secret_key.lower() in {"dev", "secret", "changeme", "default"}:
        raise RuntimeError(
            "Production configuration error: Strong SECRET_KEY is required in production."
        )

    # 11. Production Database URL validation
    db_url_val = _get("SQLALCHEMY_DATABASE_URI", None)
    if db_url_val is None:
        db_url_val = _get("DATABASE_URL", None)
    if db_url_val is not None:
        db_url = str(db_url_val).strip()
        if not db_url or db_url.startswith("sqlite"):
            raise RuntimeError(
                "Production configuration error: Production database URL (MySQL/PostgreSQL) is required in production; SQLite cannot be used."
            )


if is_production_mode(Config):
    validate_production_config(Config)
