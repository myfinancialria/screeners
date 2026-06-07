"""
Send email via SMTP (Gmail by default). Credentials live in .env:

    EMAIL_FROM           your gmail address
    EMAIL_APP_PASSWORD   a Gmail *App Password* (NOT your normal password)
    EMAIL_TO             recipient (defaults to EMAIL_FROM)
    SMTP_HOST            smtp.gmail.com (default)
    SMTP_PORT            465 (default, SSL)
"""
import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage

from envtools import load_env


def send_email(subject: str, html_body: str, attachments=None) -> None:
    env = load_env()
    host = env.get("SMTP_HOST", "smtp.gmail.com")
    port = int(env.get("SMTP_PORT", "465"))
    user = env.get("EMAIL_FROM", "").strip()
    pwd = env.get("EMAIL_APP_PASSWORD", "").strip()
    to = env.get("EMAIL_TO", user).strip() or user

    if not user or not pwd:
        raise SystemExit("Set EMAIL_FROM and EMAIL_APP_PASSWORD in .env "
                         "(use a Gmail App Password).")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content("This report is HTML — open in an HTML-capable mail client.")
    msg.add_alternative(html_body, subtype="html")

    for path in attachments or []:
        if not os.path.exists(path):
            continue
        ctype, _ = mimetypes.guess_type(path)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        with open(path, "rb") as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype,
                               filename=os.path.basename(path))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=ctx) as s:
        s.login(user, pwd)
        s.send_message(msg)
