"""Gmail API client supporting two accounts (personal + professional)."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import structlog
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from utils.resilience import get_service_health_registry

log = structlog.get_logger()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


class GmailClient:
    """Async Gmail API client.

    Each instance wraps one OAuth credential set (personal **or**
    professional).  Synchronous ``googleapiclient`` calls are offloaded
    to a thread via :func:`asyncio.to_thread` so the event loop is never
    blocked.

    **Credentials format** (``credentials_json_b64``): a base64-encoded
    JSON blob containing at minimum::

        {
            "token": "ya29.…",
            "refresh_token": "1//…",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "….apps.googleusercontent.com",
            "client_secret": "GOCSPX-…",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly", …]
        }

    Token refresh (pitfall #11): on every public method call the
    credentials are checked; if expired the google-auth library
    refreshes automatically and the updated blob is stored via the
    ``on_token_refresh`` callback so the caller can persist it.
    """

    def __init__(
        self,
        credentials_json_b64: str,
        account_name: str,
        on_token_refresh: Any | None = None,
    ) -> None:
        self._account = account_name
        self._on_token_refresh = on_token_refresh

        cred_data = json.loads(base64.b64decode(credentials_json_b64))
        self._creds = Credentials(
            token=cred_data.get("token"),
            refresh_token=cred_data.get("refresh_token"),
            token_uri=cred_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=cred_data.get("client_id"),
            client_secret=cred_data.get("client_secret"),
            scopes=cred_data.get("scopes", SCOPES),
        )

        self._service = build("gmail", "v1", credentials=self._creds)
        self._cb = get_service_health_registry().get_or_create(f"gmail_{account_name}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_unread_emails(
        self, max_results: int = 50, since_hours: int = 12,
    ) -> list[dict[str, Any]]:
        """Fetch unread emails from the last *since_hours* hours."""
        await self._ensure_valid_creds()

        since_epoch = int(
            (datetime.now(timezone.utc).timestamp()) - since_hours * 3600
        )
        query = f"is:unread after:{since_epoch}"

        messages_ref = await asyncio.to_thread(
            self._service.users().messages().list(
                userId="me", q=query, maxResults=max_results,
            ).execute,
        )

        msg_stubs = messages_ref.get("messages", [])
        results: list[dict[str, Any]] = []

        for stub in msg_stubs:
            msg = await asyncio.to_thread(
                self._service.users().messages().get(
                    userId="me", id=stub["id"], format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"],
                ).execute,
            )
            results.append(self._parse_metadata(msg))

        self._cb.record_success()
        log.info(
            "gmail_unread_fetched",
            account=self._account,
            count=len(results),
            since_hours=since_hours,
        )
        return results

    async def get_email_body(self, message_id: str) -> str:
        """Fetch the full body of an email as plain text."""
        await self._ensure_valid_creds()

        msg = await asyncio.to_thread(
            self._service.users().messages().get(
                userId="me", id=message_id, format="full",
            ).execute,
        )
        return _extract_body(msg.get("payload", {}))

    async def label_email(
        self,
        message_id: str,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> None:
        """Add / remove labels from a message."""
        await self._ensure_valid_creds()

        body: dict[str, Any] = {}
        if add_labels:
            body["addLabelIds"] = add_labels
        if remove_labels:
            body["removeLabelIds"] = remove_labels

        await asyncio.to_thread(
            self._service.users().messages().modify(
                userId="me", id=message_id, body=body,
            ).execute,
        )
        log.info(
            "gmail_labels_modified",
            account=self._account,
            message_id=message_id,
            added=add_labels,
            removed=remove_labels,
        )

    async def archive_email(self, message_id: str) -> None:
        """Archive an email (remove INBOX label)."""
        await self.label_email(message_id, remove_labels=["INBOX"])

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str | None = None,
        reply_to_message_id: str | None = None,
    ) -> dict[str, Any]:
        """Send an email.

        .. warning::

            The caller **must** route this through the approval queue
            first (HC-01).  This method does not enforce approval — it
            only handles the Gmail API mechanics.
        """
        await self._ensure_valid_creds()

        mime = MIMEText(body)
        mime["to"] = to
        mime["subject"] = subject
        if cc:
            mime["cc"] = cc

        send_body: dict[str, Any] = {
            "raw": base64.urlsafe_b64encode(mime.as_bytes()).decode(),
        }

        if reply_to_message_id:
            # Fetch the original to get its threadId
            original = await asyncio.to_thread(
                self._service.users().messages().get(
                    userId="me", id=reply_to_message_id, format="metadata",
                    metadataHeaders=[],
                ).execute,
            )
            send_body["threadId"] = original.get("threadId")

        result = await asyncio.to_thread(
            self._service.users().messages().send(
                userId="me", body=send_body,
            ).execute,
        )

        log.info(
            "gmail_email_sent",
            account=self._account,
            to=to,
            subject=subject,
            message_id=result.get("id"),
        )
        return {
            "message_id": result.get("id"),
            "thread_id": result.get("threadId"),
        }

    async def send_email_with_attachment(
        self,
        to: str,
        subject: str,
        body: str,
        attachment_path: str,
        attachment_name: str | None = None,
        cc: str | None = None,
    ) -> dict[str, Any]:
        """Send an email with a file attachment.

        .. warning::

            The caller **must** route this through the approval queue
            first (HC-01).
        """
        await self._ensure_valid_creds()

        msg = MIMEMultipart()
        msg["to"] = to
        msg["subject"] = subject
        if cc:
            msg["cc"] = cc

        msg.attach(MIMEText(body))

        # Read and attach file
        file_path = Path(attachment_path)
        file_bytes = await asyncio.to_thread(file_path.read_bytes)
        name = attachment_name or file_path.name
        attachment = MIMEApplication(file_bytes, Name=name)
        attachment["Content-Disposition"] = f'attachment; filename="{name}"'
        msg.attach(attachment)

        send_body: dict[str, Any] = {
            "raw": base64.urlsafe_b64encode(msg.as_bytes()).decode(),
        }

        result = await asyncio.to_thread(
            self._service.users().messages().send(
                userId="me", body=send_body,
            ).execute,
        )

        log.info(
            "gmail_email_sent_with_attachment",
            account=self._account,
            to=to,
            subject=subject,
            attachment=name,
            message_id=result.get("id"),
        )
        return {
            "message_id": result.get("id"),
            "thread_id": result.get("threadId"),
        }

    async def close(self) -> None:
        """Clean up resources (the underlying httplib2 transport)."""
        self._service.close()

    # ------------------------------------------------------------------
    # Credentials management (pitfall #11)
    # ------------------------------------------------------------------

    async def _ensure_valid_creds(self) -> None:
        """Refresh credentials if expired, then persist the new token."""
        if self._creds.valid:
            return

        if self._creds.expired and self._creds.refresh_token:
            await asyncio.to_thread(self._creds.refresh, Request())

            log.info("gmail_token_refreshed", account=self._account)

            if self._on_token_refresh:
                new_blob = self._serialise_creds()
                await self._on_token_refresh(self._account, new_blob)
        else:
            log.error(
                "gmail_creds_invalid",
                account=self._account,
                expired=self._creds.expired,
                has_refresh=bool(self._creds.refresh_token),
            )
            raise RuntimeError(
                f"Gmail credentials for {self._account} are invalid and "
                "cannot be refreshed."
            )

    def _serialise_creds(self) -> str:
        """Return a base64-encoded JSON blob of the current credentials."""
        data = {
            "token": self._creds.token,
            "refresh_token": self._creds.refresh_token,
            "token_uri": self._creds.token_uri,
            "client_id": self._creds.client_id,
            "client_secret": self._creds.client_secret,
            "scopes": list(self._creds.scopes or SCOPES),
        }
        return base64.b64encode(json.dumps(data).encode()).decode()

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_metadata(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Extract a clean dict from a Gmail metadata-format message."""
        headers = {
            h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }

        from_raw = headers.get("from", "")
        from_name, from_address = _parse_from(from_raw)

        has_attachments = any(
            part.get("filename")
            for part in msg.get("payload", {}).get("parts", [])
        )

        return {
            "message_id": msg["id"],
            "from_address": from_address,
            "from_name": from_name,
            "to": headers.get("to", ""),
            "subject": headers.get("subject", "(no subject)"),
            "snippet": msg.get("snippet", ""),
            "date": headers.get("date", ""),
            "labels": msg.get("labelIds", []),
            "has_attachments": has_attachments,
        }


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _parse_from(raw: str) -> tuple[str, str]:
    """Parse ``"Display Name <email@example.com>"`` into (name, address)."""
    if "<" in raw and ">" in raw:
        name = raw[: raw.index("<")].strip().strip('"')
        address = raw[raw.index("<") + 1 : raw.index(">")].strip()
        return name, address
    return "", raw.strip()


def _extract_body(payload: dict[str, Any]) -> str:
    """Recursively extract the plain-text body from a Gmail payload."""
    mime_type = payload.get("mimeType", "")

    # Leaf node with text/plain
    if mime_type == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode(
            "utf-8", errors="replace"
        )

    # Multipart — recurse, prefer text/plain
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode(
                    "utf-8", errors="replace"
                )

    # Fallback: recurse into nested multipart
    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result

    return ""
