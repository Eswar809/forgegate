"""Email task ingress using only the standard library.

Poll an IMAP mailbox for unseen messages, run each one through the agent, and
reply over SMTP. Configuration comes from the environment:

    FORGEGATE_IMAP_HOST, FORGEGATE_IMAP_PORT (default 993)
    FORGEGATE_SMTP_HOST, FORGEGATE_SMTP_PORT (default 465)
    FORGEGATE_EMAIL_USER, FORGEGATE_EMAIL_PASSWORD
    FORGEGATE_EMAIL_MAILBOX (default INBOX)
    FORGEGATE_EMAIL_SSL (default 1)

The network paths are deliberately thin; ``parse_message`` is pure and tested.
"""

from __future__ import annotations

import email
import imaplib
import os
import smtplib
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any


def _decode(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, LookupError):
        return value


def parse_message(raw: bytes) -> dict[str, str]:
    """Extract sender, subject, message id and plain-text body."""
    message = email.message_from_bytes(raw)
    body = ""
    if message.is_multipart():
        for part in message.walk():
            disposition = str(part.get("Content-Disposition", ""))
            if part.get_content_type() == "text/plain" and "attachment" not in disposition:
                payload = part.get_payload(decode=True) or b""
                body = payload.decode(
                    part.get_content_charset() or "utf-8", "replace"
                )
                break
    else:
        payload = message.get_payload(decode=True) or b""
        body = payload.decode(message.get_content_charset() or "utf-8", "replace")
    return {
        "from": parseaddr(str(message.get("From", "")))[1],
        "subject": _decode(str(message.get("Subject", ""))),
        "message_id": str(message.get("Message-ID", "")),
        "body": body.strip(),
    }


@dataclass
class EmailConfig:
    imap_host: str
    smtp_host: str
    username: str
    password: str
    imap_port: int = 993
    smtp_port: int = 465
    mailbox: str = "INBOX"
    use_ssl: bool = True

    @classmethod
    def from_env(cls) -> "EmailConfig":
        required = {
            "imap_host": os.environ.get("FORGEGATE_IMAP_HOST", ""),
            "smtp_host": os.environ.get("FORGEGATE_SMTP_HOST", ""),
            "username": os.environ.get("FORGEGATE_EMAIL_USER", ""),
            "password": os.environ.get("FORGEGATE_EMAIL_PASSWORD", ""),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"missing email settings: {', '.join(missing)}")
        return cls(
            **required,
            imap_port=int(os.environ.get("FORGEGATE_IMAP_PORT", "993")),
            smtp_port=int(os.environ.get("FORGEGATE_SMTP_PORT", "465")),
            mailbox=os.environ.get("FORGEGATE_EMAIL_MAILBOX", "INBOX"),
            use_ssl=os.environ.get("FORGEGATE_EMAIL_SSL", "1") != "0",
        )


class EmailPoller:
    def __init__(self, config: EmailConfig) -> None:
        self.config = config

    def _imap(self) -> imaplib.IMAP4:
        if self.config.use_ssl:
            return imaplib.IMAP4_SSL(
                self.config.imap_host, self.config.imap_port
            )
        return imaplib.IMAP4(self.config.imap_host, self.config.imap_port)

    def fetch_unseen(self, limit: int = 10) -> list[dict[str, str]]:
        """Return unseen messages without marking them read."""
        client = self._imap()
        try:
            client.login(self.config.username, self.config.password)
            client.select(self.config.mailbox)
            status, data = client.search(None, "UNSEEN")
            if status != "OK":
                return []
            uids = (data[0] or b"").split()[-limit:]
            messages: list[dict[str, str]] = []
            for uid in uids:
                status, parts = client.fetch(uid, "(BODY.PEEK[])")
                if status != "OK" or not parts:
                    continue
                raw = next(
                    (part[1] for part in parts if isinstance(part, tuple)), b""
                )
                parsed = parse_message(raw)
                parsed["uid"] = uid.decode("ascii", "replace")
                messages.append(parsed)
            return messages
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def mark_seen(self, uid: str) -> None:
        client = self._imap()
        try:
            client.login(self.config.username, self.config.password)
            client.select(self.config.mailbox)
            client.store(uid, "+FLAGS", "\\Seen")
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def reply(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.config.username
        message["To"] = to
        message["Subject"] = (
            subject if subject.lower().startswith("re:") else f"Re: {subject}"
        )
        message.set_content(body)
        if self.config.use_ssl:
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                self.config.smtp_host, self.config.smtp_port, timeout=30
            )
        else:
            client = smtplib.SMTP(
                self.config.smtp_host, self.config.smtp_port, timeout=30
            )
        try:
            client.login(self.config.username, self.config.password)
            client.send_message(message)
        finally:
            client.quit()


def task_from_email(message: dict[str, str]) -> dict[str, Any]:
    """Wrap an email as a ForgeGate task (live model expected)."""
    subject = message.get("subject", "").strip()
    body = message.get("body", "").strip()
    text = f"{subject}\n\n{body}".strip() if subject else body
    return {
        "id": f"email-{message.get('uid', message.get('message_id', '0'))}"[:80],
        "description": text[:4000],
        "tool_name": "email-task",
        "args": {},
        "tests": [],
        "expected_contains": [],
        "tags": ["email"],
    }
