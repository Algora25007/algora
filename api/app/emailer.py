"""Pluggable email sending. Dev and tests use the console sender; production uses SMTP."""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from .config import Settings

log = logging.getLogger("algora.email")


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    def send(self, message: OutgoingEmail) -> None: ...


class ConsoleEmailSender:
    """Logs emails instead of sending them and keeps the last 100 in ``outbox`` (dev/test only)."""

    def __init__(self) -> None:
        self.outbox: list[OutgoingEmail] = []

    def send(self, message: OutgoingEmail) -> None:
        self.outbox.append(message)
        del self.outbox[:-100]
        log.warning("DEV EMAIL to=%s subject=%s\n%s", message.to, message.subject, message.body)


class SmtpEmailSender:  # pragma: no cover - exercised against a real SMTP server, not in unit tests
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def send(self, message: OutgoingEmail) -> None:
        msg = EmailMessage()
        msg["From"] = self.s.email_from
        msg["To"] = message.to
        msg["Subject"] = message.subject
        msg.set_content(message.body)
        with smtplib.SMTP(self.s.smtp_host or "", self.s.smtp_port, timeout=15) as smtp:
            if self.s.smtp_starttls:
                smtp.starttls()
            if self.s.smtp_username:
                pw = self.s.smtp_password.get_secret_value() if self.s.smtp_password else ""
                smtp.login(self.s.smtp_username, pw)
            smtp.send_message(msg)


def build_sender(settings: Settings) -> EmailSender:
    if settings.email_backend == "smtp":  # pragma: no cover
        return SmtpEmailSender(settings)
    return ConsoleEmailSender()


def otp_email(to: str, code: str, ttl_minutes: int) -> OutgoingEmail:
    return OutgoingEmail(
        to=to,
        subject=f"Your Algora sign-in code: {code}",
        body=(
            f"Your Algora sign-in code is {code}.\n\n"
            f"It expires in {ttl_minutes} minutes. If you didn't ask for it, ignore this email; "
            "nobody can sign in without your authenticator app as well.\n"
        ),
    )
