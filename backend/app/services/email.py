"""Sends the three emails the account system needs."""
import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Deque

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class OutgoingEmail:
    """One email, as built here and handed to a sender."""

    to: str
    subject: str
    body: str


OUTBOX: Deque[OutgoingEmail] = deque(maxlen=50)


def _send_console(message: OutgoingEmail) -> None:
    """Log the email instead of sending it. The default."""
    OUTBOX.append(message)
    logger.warning(
        "[email:console] To: %s | Subject: %s\n%s", message.to, message.subject, message.body
    )


def _send_smtp(message: OutgoingEmail) -> None:
    """Deliver through the configured SMTP server. Blocking -- always called via"""
    import smtplib

    email = EmailMessage()
    email["From"] = settings.EMAIL_FROM
    email["To"] = message.to
    email["Subject"] = message.subject
    email.set_content(message.body)

    if settings.SMTP_USE_SSL:
        server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20)
    else:
        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20)
    try:
        if settings.SMTP_USE_TLS and not settings.SMTP_USE_SSL:
            server.starttls()
        if settings.SMTP_USERNAME:
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        server.send_message(email)
    finally:
        server.quit()


async def send_email(message: OutgoingEmail) -> bool:
    """Send one email with whichever sender is configured."""
    try:
        if settings.email_backend == "smtp":
            await asyncio.to_thread(_send_smtp, message)
        else:
            _send_console(message)
        return True
    except Exception:  # noqa: BLE001 -- deliberately broad, see docstring
        logger.exception("Failed to send email to %s (%s)", message.to, message.subject)
        return False


def invite_link(token: str) -> str:
    """Where an invite email points. The frontend's signup page reads the"""
    return f"{settings.frontend_url}/login?invite={token}"


def reset_link(token: str) -> str:
    return f"{settings.frontend_url}/reset-password?token={token}"


def verify_link(token: str) -> str:
    return f"{settings.frontend_url}/verify-email?token={token}"


async def send_invite_email(to: str, org_name: str, role: str, token: str) -> bool:
    return await send_email(
        OutgoingEmail(
            to=to,
            subject=f"You have been invited to join {org_name} on {settings.PROJECT_NAME}",
            body=(
                f"You have been invited to join {org_name} on "
                f"{settings.PROJECT_NAME} as {role}.\n\n"
                f"Accept the invitation and create your account here:\n"
                f"{invite_link(token)}\n\n"
                f"This link expires in {settings.INVITE_EXPIRE_DAYS} days. If "
                f"you were not expecting it, you can ignore this email."
            ),
        )
    )


async def send_password_reset_email(to: str, token: str) -> bool:
    return await send_email(
        OutgoingEmail(
            to=to,
            subject=f"Reset your {settings.PROJECT_NAME} password",
            body=(
                "Somebody (hopefully you) asked to reset the password for this "
                "account.\n\n"
                f"Choose a new password here:\n{reset_link(token)}\n\n"
                f"This link works once and expires in "
                f"{settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES} minutes. If you "
                f"did not ask for it, ignore this email -- your password has "
                f"not been changed."
            ),
        )
    )


async def send_verification_email(to: str, token: str) -> bool:
    return await send_email(
        OutgoingEmail(
            to=to,
            subject=f"Confirm your email address for {settings.PROJECT_NAME}",
            body=(
                f"Welcome to {settings.PROJECT_NAME}.\n\n"
                f"Confirm your email address here:\n{verify_link(token)}\n\n"
                f"This link expires in "
                f"{settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS} hours."
            ),
        )
    )
