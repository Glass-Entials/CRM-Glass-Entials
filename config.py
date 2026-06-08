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
        except Exception as e:
            logging.error("Could not load AWS secrets, falling back to env")
            SECRET_KEY = os.environ.get("SECRET_KEY")
            SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
            GST_CLIENT_ID = os.environ.get("GST_CLIENT_ID")
            GST_CLIENT_SECRET = os.environ.get("GST_CLIENT_SECRET")
    else:
        SECRET_KEY = os.environ.get("SECRET_KEY")
        SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
        GST_CLIENT_ID = os.environ.get("GST_CLIENT_ID")
        GST_CLIENT_SECRET = os.environ.get("GST_CLIENT_SECRET")

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
