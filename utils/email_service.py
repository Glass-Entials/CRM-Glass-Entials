"""
utils/email_service.py
Enterprise-grade email utility for the GlassEntials CRM.
Sends HTML emails via SMTP with proper error handling and logging.
"""

import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

logger = logging.getLogger(__name__)


def _get_smtp_config():
    return {
        "host":     os.environ.get("MAIL_SERVER", "smtp.gmail.com"),
        "port":     int(os.environ.get("MAIL_PORT", 587)),
        "use_tls":  os.environ.get("MAIL_USE_TLS", "true").lower() == "true",
        "username": os.environ.get("MAIL_USERNAME", ""),
        "password": os.environ.get("MAIL_PASSWORD", ""),
        "sender":   os.environ.get("MAIL_DEFAULT_SENDER", "noreply@glassentials.in"),
        "sender_name": os.environ.get("MAIL_SENDER_NAME", "GlassEntials CRM"),
    }


def send_html_email(to_email: str, subject: str, html_body: str, text_body: str = None) -> bool:
    """
    Send an HTML email.  Falls back to text/plain if html_body is not given.
    Returns True on success, False on failure (and logs the error).
    """
    cfg = _get_smtp_config()

    if not cfg["username"] or not cfg["password"]:
        logger.error("[Email] MAIL_USERNAME / MAIL_PASSWORD not configured — email not sent.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"]    = subject
        msg["From"]       = f'{cfg["sender_name"]} <{cfg["sender"]}>'
        msg["To"]         = to_email
        msg["Date"]       = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain=cfg["sender"].split("@")[-1])

        if text_body:
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as server:
            server.ehlo()
            if cfg["use_tls"]:
                server.starttls()
                server.ehlo()
            server.login(cfg["username"], cfg["password"])
            server.sendmail(cfg["sender"], [to_email], msg.as_string())

        logger.info(f"[Email] Sent '{subject}' to {to_email}")
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error("[Email] SMTP authentication failed — check MAIL_USERNAME / MAIL_PASSWORD.")
    except smtplib.SMTPException as exc:
        logger.error(f"[Email] SMTP error: {exc}")
    except Exception as exc:
        logger.error(f"[Email] Unexpected error: {exc}")

    return False
