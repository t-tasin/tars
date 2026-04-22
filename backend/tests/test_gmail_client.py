"""Tests for GmailClient with mocked Gmail API service."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.gmail_client import GmailClient, _extract_body, _parse_from

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_creds_b64(**overrides: str) -> str:
    """Build a base64-encoded credentials blob."""
    data = {
        "token": "ya29.test-token",
        "refresh_token": "1//test-refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "test-client-id.apps.googleusercontent.com",
        "client_secret": "GOCSPX-test-secret",
        "scopes": [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.modify",
        ],
    }
    data.update(overrides)
    return base64.b64encode(json.dumps(data).encode()).decode()


def _make_message(
    msg_id: str = "msg-1",
    from_: str = "Alice <alice@example.com>",
    to: str = "tasin@example.com",
    subject: str = "Hello",
    snippet: str = "Preview text here",
    labels: list[str] | None = None,
    parts: list[dict] | None = None,
) -> dict:
    """Build a fake Gmail message dict (metadata format)."""
    return {
        "id": msg_id,
        "threadId": "thread-1",
        "snippet": snippet,
        "labelIds": labels or ["INBOX", "UNREAD"],
        "payload": {
            "headers": [
                {"name": "From", "value": from_},
                {"name": "To", "value": to},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Mon, 10 Mar 2026 14:30:00 +0000"},
            ],
            "parts": parts or [],
        },
    }


def _b64_body(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_service():
    """Create a mock Gmail API service."""
    service = MagicMock()
    return service


@pytest.fixture
def client(mock_service) -> GmailClient:
    """GmailClient with mocked credentials and service."""
    creds_b64 = _make_creds_b64()

    with patch("integrations.gmail_client.build", return_value=mock_service):
        c = GmailClient(
            credentials_json_b64=creds_b64,
            account_name="personal",
        )
    # Mark creds as valid so _ensure_valid_creds is a no-op
    c._creds = MagicMock()
    c._creds.valid = True
    return c


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


def test_init_decodes_credentials():
    creds_b64 = _make_creds_b64()
    with patch("integrations.gmail_client.build") as mock_build:
        client = GmailClient(creds_b64, "professional")

    assert client._account == "professional"
    mock_build.assert_called_once_with("gmail", "v1", credentials=client._creds)


def test_init_bad_base64_raises():
    with pytest.raises(Exception):
        with patch("integrations.gmail_client.build"):
            GmailClient("not-valid-base64!!!", "test")


# ---------------------------------------------------------------------------
# get_unread_emails()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_unread_emails(client: GmailClient, mock_service):
    msg = _make_message()

    list_mock = MagicMock()
    list_mock.execute.return_value = {"messages": [{"id": "msg-1"}]}
    get_mock = MagicMock()
    get_mock.execute.return_value = msg

    mock_service.users.return_value.messages.return_value.list.return_value = list_mock
    mock_service.users.return_value.messages.return_value.get.return_value = get_mock

    results = await client.get_unread_emails(max_results=10, since_hours=6)

    assert len(results) == 1
    email = results[0]
    assert email["message_id"] == "msg-1"
    assert email["from_address"] == "alice@example.com"
    assert email["from_name"] == "Alice"
    assert email["to"] == "tasin@example.com"
    assert email["subject"] == "Hello"
    assert email["snippet"] == "Preview text here"
    assert "UNREAD" in email["labels"]
    assert email["has_attachments"] is False


@pytest.mark.asyncio
async def test_get_unread_emails_empty(client: GmailClient, mock_service):
    list_mock = MagicMock()
    list_mock.execute.return_value = {}

    mock_service.users.return_value.messages.return_value.list.return_value = list_mock

    results = await client.get_unread_emails()
    assert results == []


@pytest.mark.asyncio
async def test_get_unread_emails_detects_attachments(client: GmailClient, mock_service):
    msg = _make_message(parts=[{"filename": "report.pdf", "mimeType": "application/pdf"}])

    list_mock = MagicMock()
    list_mock.execute.return_value = {"messages": [{"id": "msg-1"}]}
    get_mock = MagicMock()
    get_mock.execute.return_value = msg

    mock_service.users.return_value.messages.return_value.list.return_value = list_mock
    mock_service.users.return_value.messages.return_value.get.return_value = get_mock

    results = await client.get_unread_emails()
    assert results[0]["has_attachments"] is True


# ---------------------------------------------------------------------------
# get_email_body()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_email_body_plain_text(client: GmailClient, mock_service):
    body_text = "Hello, this is the email body."
    msg = {
        "payload": {
            "mimeType": "text/plain",
            "body": {"data": _b64_body(body_text)},
        }
    }

    get_mock = MagicMock()
    get_mock.execute.return_value = msg
    mock_service.users.return_value.messages.return_value.get.return_value = get_mock

    result = await client.get_email_body("msg-1")
    assert result == body_text


@pytest.mark.asyncio
async def test_get_email_body_multipart(client: GmailClient, mock_service):
    body_text = "Plain text part."
    msg = {
        "payload": {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64_body(body_text)},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64_body("<p>HTML part</p>")},
                },
            ],
        }
    }

    get_mock = MagicMock()
    get_mock.execute.return_value = msg
    mock_service.users.return_value.messages.return_value.get.return_value = get_mock

    result = await client.get_email_body("msg-1")
    assert result == body_text


@pytest.mark.asyncio
async def test_get_email_body_empty_returns_empty_string(client: GmailClient, mock_service):
    msg = {"payload": {"mimeType": "multipart/mixed", "parts": []}}

    get_mock = MagicMock()
    get_mock.execute.return_value = msg
    mock_service.users.return_value.messages.return_value.get.return_value = get_mock

    result = await client.get_email_body("msg-1")
    assert result == ""


# ---------------------------------------------------------------------------
# label_email()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_label_email(client: GmailClient, mock_service):
    modify_mock = MagicMock()
    modify_mock.execute.return_value = {}
    mock_service.users.return_value.messages.return_value.modify.return_value = modify_mock

    await client.label_email("msg-1", add_labels=["STARRED"], remove_labels=["UNREAD"])

    mock_service.users.return_value.messages.return_value.modify.assert_called_once()
    call_kwargs = mock_service.users.return_value.messages.return_value.modify.call_args
    body = call_kwargs.kwargs.get("body", call_kwargs[1].get("body"))
    assert body["addLabelIds"] == ["STARRED"]
    assert body["removeLabelIds"] == ["UNREAD"]


# ---------------------------------------------------------------------------
# archive_email()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_email(client: GmailClient, mock_service):
    modify_mock = MagicMock()
    modify_mock.execute.return_value = {}
    mock_service.users.return_value.messages.return_value.modify.return_value = modify_mock

    await client.archive_email("msg-1")

    call_kwargs = mock_service.users.return_value.messages.return_value.modify.call_args
    body = call_kwargs.kwargs.get("body", call_kwargs[1].get("body"))
    assert "INBOX" in body["removeLabelIds"]


# ---------------------------------------------------------------------------
# send_email()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email(client: GmailClient, mock_service):
    send_mock = MagicMock()
    send_mock.execute.return_value = {"id": "sent-1", "threadId": "thread-new"}
    mock_service.users.return_value.messages.return_value.send.return_value = send_mock

    result = await client.send_email(
        to="prof@university.edu",
        subject="Following up",
        body="Dear Professor, ...",
    )

    assert result["message_id"] == "sent-1"
    assert result["thread_id"] == "thread-new"

    call_kwargs = mock_service.users.return_value.messages.return_value.send.call_args
    send_body = call_kwargs.kwargs.get("body", call_kwargs[1].get("body"))
    # Verify it has a base64-encoded raw field
    assert "raw" in send_body
    raw_decoded = base64.urlsafe_b64decode(send_body["raw"]).decode()
    assert "prof@university.edu" in raw_decoded
    assert "Following up" in raw_decoded


@pytest.mark.asyncio
async def test_send_email_with_cc(client: GmailClient, mock_service):
    send_mock = MagicMock()
    send_mock.execute.return_value = {"id": "sent-2", "threadId": "thread-2"}
    mock_service.users.return_value.messages.return_value.send.return_value = send_mock

    await client.send_email(
        to="a@example.com",
        subject="Test",
        body="Body",
        cc="b@example.com",
    )

    call_kwargs = mock_service.users.return_value.messages.return_value.send.call_args
    raw = base64.urlsafe_b64decode(call_kwargs.kwargs.get("body", call_kwargs[1].get("body"))["raw"]).decode()
    assert "b@example.com" in raw


@pytest.mark.asyncio
async def test_send_email_reply(client: GmailClient, mock_service):
    # get() for the original message to fetch threadId
    get_mock = MagicMock()
    get_mock.execute.return_value = {"id": "orig-1", "threadId": "thread-orig"}
    mock_service.users.return_value.messages.return_value.get.return_value = get_mock

    send_mock = MagicMock()
    send_mock.execute.return_value = {"id": "sent-reply", "threadId": "thread-orig"}
    mock_service.users.return_value.messages.return_value.send.return_value = send_mock

    result = await client.send_email(
        to="a@example.com",
        subject="Re: Original",
        body="Reply body",
        reply_to_message_id="orig-1",
    )

    # Verify threadId is included in the send body
    call_kwargs = mock_service.users.return_value.messages.return_value.send.call_args
    send_body = call_kwargs.kwargs.get("body", call_kwargs[1].get("body"))
    assert send_body["threadId"] == "thread-orig"


# ---------------------------------------------------------------------------
# Token refresh (pitfall #11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_refresh_on_expired_creds(mock_service):
    creds_b64 = _make_creds_b64()

    with patch("integrations.gmail_client.build", return_value=mock_service):
        client = GmailClient(creds_b64, "personal")

    # Simulate expired credentials
    client._creds = MagicMock()
    client._creds.valid = False
    client._creds.expired = True
    client._creds.refresh_token = "1//test-refresh"
    client._creds.token = "new-token"
    client._creds.token_uri = "https://oauth2.googleapis.com/token"
    client._creds.client_id = "cid"
    client._creds.client_secret = "csec"
    client._creds.scopes = set()

    list_mock = MagicMock()
    list_mock.execute.return_value = {}
    mock_service.users.return_value.messages.return_value.list.return_value = list_mock

    await client.get_unread_emails()

    # refresh() should have been called
    client._creds.refresh.assert_called_once()


@pytest.mark.asyncio
async def test_token_refresh_calls_callback(mock_service):
    creds_b64 = _make_creds_b64()
    callback = AsyncMock()

    with patch("integrations.gmail_client.build", return_value=mock_service):
        client = GmailClient(creds_b64, "professional", on_token_refresh=callback)

    client._creds = MagicMock()
    client._creds.valid = False
    client._creds.expired = True
    client._creds.refresh_token = "1//test"
    client._creds.token = "refreshed-token"
    client._creds.token_uri = "https://oauth2.googleapis.com/token"
    client._creds.client_id = "cid"
    client._creds.client_secret = "csec"
    client._creds.scopes = set()

    list_mock = MagicMock()
    list_mock.execute.return_value = {}
    mock_service.users.return_value.messages.return_value.list.return_value = list_mock

    await client.get_unread_emails()

    callback.assert_awaited_once()
    call_args = callback.call_args[0]
    assert call_args[0] == "professional"
    # Second arg is the new b64-encoded creds
    decoded = json.loads(base64.b64decode(call_args[1]))
    assert decoded["token"] == "refreshed-token"


@pytest.mark.asyncio
async def test_invalid_creds_no_refresh_token_raises(mock_service):
    creds_b64 = _make_creds_b64()

    with patch("integrations.gmail_client.build", return_value=mock_service):
        client = GmailClient(creds_b64, "personal")

    client._creds = MagicMock()
    client._creds.valid = False
    client._creds.expired = True
    client._creds.refresh_token = None

    with pytest.raises(RuntimeError, match="invalid"):
        await client.get_unread_emails()


# ---------------------------------------------------------------------------
# _parse_from helper
# ---------------------------------------------------------------------------


def test_parse_from_name_and_email():
    name, addr = _parse_from("Alice Smith <alice@example.com>")
    assert name == "Alice Smith"
    assert addr == "alice@example.com"


def test_parse_from_quoted_name():
    name, addr = _parse_from('"Bob Jones" <bob@example.com>')
    assert name == "Bob Jones"
    assert addr == "bob@example.com"


def test_parse_from_email_only():
    name, addr = _parse_from("solo@example.com")
    assert name == ""
    assert addr == "solo@example.com"


# ---------------------------------------------------------------------------
# _extract_body helper
# ---------------------------------------------------------------------------


def test_extract_body_plain_leaf():
    payload = {
        "mimeType": "text/plain",
        "body": {"data": _b64_body("Hello world")},
    }
    assert _extract_body(payload) == "Hello world"


def test_extract_body_nested_multipart():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": _b64_body("Nested plain")},
                    },
                ],
            },
        ],
    }
    assert _extract_body(payload) == "Nested plain"


def test_extract_body_empty_payload():
    assert _extract_body({}) == ""
