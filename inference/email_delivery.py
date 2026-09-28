"""TLS-only email delivery; ambiguous sends are not automatically duplicated."""

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from app.core.config import Settings
from app.saas.security import normalized_email


async def deliver_email(
    recipient: str, payload: dict[str, Any], identifier: str, settings: Settings
) -> tuple[str, str | None]:
    if not settings.smtp_host or not settings.smtp_from:
        return "failed", "Email transport not configured"

    def send() -> None:
        message = EmailMessage()
        message["To"] = normalized_email(recipient)
        message["From"] = normalized_email(settings.smtp_from)
        message["Subject"] = "Sentinel incident requiring review"
        message["Message-ID"] = f"<sentinel-{identifier}@{message['From'].split('@')[-1]}>"
        message.set_content(
            f"Incident {payload['incident_id']}\nHost {payload['host']}\n"
            f"Failure probability {payload['failure_probability']:.6f}\n"
            f"{payload['model_validation_warning']}\n{payload['summary']['summary']}\n"
            f"{payload['dashboard_link']}"
        )
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=5) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            smtp.send_message(message)

    try:
        await asyncio.to_thread(send)
        return "delivered", None
    except Exception:
        return "unknown", "Email transport failed; outcome unknown; no automatic retry"
