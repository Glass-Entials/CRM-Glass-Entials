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
            PHONEPE_MERCHANT_ID = secrets.get("PHONEPE_MERCHANT_ID") or os.environ.get("PHONEPE_MERCHANT_ID", "PGTESTPAYUAT")
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
        except Exception as e:
            logging.error("Could not load AWS secrets, falling back to env")
            SECRET_KEY = os.environ.get("SECRET_KEY")
            SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
            GST_CLIENT_ID = os.environ.get("GST_CLIENT_ID")
            GST_CLIENT_SECRET = os.environ.get("GST_CLIENT_SECRET")
            RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
            RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")
            # PhonePe Payment Gateway / UPI AutoPay
            PHONEPE_MERCHANT_ID = os.environ.get("PHONEPE_MERCHANT_ID", "PGTESTPAYUAT")
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
    else:
        SECRET_KEY = os.environ.get("SECRET_KEY")
        SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
        GST_CLIENT_ID = os.environ.get("GST_CLIENT_ID")
        GST_CLIENT_SECRET = os.environ.get("GST_CLIENT_SECRET")
        RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
        RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")
        # PhonePe Payment Gateway / UPI AutoPay
        PHONEPE_MERCHANT_ID = os.environ.get("PHONEPE_MERCHANT_ID", "PGTESTPAYUAT")
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

    if not SECRET_KEY or SECRET_KEY == "secret123":
        raise RuntimeError("Set a strong SECRET_KEY in the environment before startup.")

    if not os.environ.get("DATABASE_URL") and not use_aws_secrets:
        raise RuntimeError("Set DATABASE_URL in the environment before startup.")

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", os.path.join(basedir, "static", "uploads"))
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 5 * 1024 * 1024))
    
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"
    PERMANENT_SESSION_LIFETIME = int(os.environ.get("PERMANENT_SESSION_LIFETIME", 28800))

    # CSRF token lifetime — set to match session (8 hours). Default is 3600s (1hr) which is too short.
    WTF_CSRF_TIME_LIMIT = int(os.environ.get("WTF_CSRF_TIME_LIMIT", 28800))
