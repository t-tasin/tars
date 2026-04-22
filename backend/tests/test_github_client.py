"""Tests for GitHubClient with mocked httpx responses."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from integrations.github_client import GitHubClient

# ---------------------------------------------------------------------------
# Fake GitHub API responses
# ---------------------------------------------------------------------------

_NOTIFICATIONS_RESPONSE = [
    {
        "id": "1001",
        "reason": "mention",
        "unread": True,
        "subject": {
            "title": "Fix parser bug",
            "type": "PullRequest",
            "url": "https://api.github.com/repos/tasin/tars/pulls/42",
        },
        "repository": {"full_name": "tasin/tars"},
        "updated_at": "2026-03-10T08:00:00Z",
    },
    {
        "id": "1002",
        "reason": "subscribed",
        "unread": False,
        "subject": {
            "title": "Add CI workflow",
            "type": "Issue",
            "url": "https://api.github.com/repos/tasin/tars/issues/7",
        },
        "repository": {"full_name": "tasin/tars"},
        "updated_at": "2026-03-09T12:00:00Z",
    },
]

_PRS_RESPONSE = [
    {
        "number": 42,
        "title": "Fix parser bug",
        "state": "open",
        "user": {"login": "tasin"},
        "draft": False,
        "labels": [{"name": "bug"}, {"name": "priority"}],
        "created_at": "2026-03-08T10:00:00Z",
        "updated_at": "2026-03-10T08:00:00Z",
        "html_url": "https://github.com/tasin/tars/pull/42",
    },
    {
        "number": 43,
        "title": "WIP: refactor orchestrator",
        "state": "open",
        "user": {"login": "tasin"},
        "draft": True,
        "labels": [],
        "created_at": "2026-03-09T14:00:00Z",
        "updated_at": "2026-03-09T16:00:00Z",
        "html_url": "https://github.com/tasin/tars/pull/43",
    },
]

_ISSUES_RESPONSE = [
    {
        "number": 7,
        "title": "Add CI workflow",
        "state": "open",
        "user": {"login": "tasin"},
        "labels": [{"name": "enhancement"}],
        "created_at": "2026-03-05T09:00:00Z",
        "updated_at": "2026-03-09T12:00:00Z",
        "html_url": "https://github.com/tasin/tars/issues/7",
    },
    {
        # This is a PR returned by the issues endpoint — should be filtered out
        "number": 42,
        "title": "Fix parser bug",
        "state": "open",
        "user": {"login": "tasin"},
        "labels": [{"name": "bug"}],
        "pull_request": {"url": "https://api.github.com/repos/tasin/tars/pulls/42"},
        "created_at": "2026-03-08T10:00:00Z",
        "updated_at": "2026-03-10T08:00:00Z",
        "html_url": "https://github.com/tasin/tars/pull/42",
    },
    {
        "number": 10,
        "title": "Database timeout on cold start",
        "state": "open",
        "user": {"login": "tasin"},
        "labels": [{"name": "bug"}],
        "created_at": "2026-03-07T11:00:00Z",
        "updated_at": "2026-03-08T15:00:00Z",
        "html_url": "https://github.com/tasin/tars/issues/10",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(json_data: list | dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "https://fake"),
    )


@pytest.fixture
def client() -> GitHubClient:
    return GitHubClient(pat="ghp_test_token_123")


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_client_sets_auth_header(client: GitHubClient):
    assert client._http.headers["Authorization"] == "token ghp_test_token_123"
    assert "application/vnd.github+json" in client._http.headers["Accept"]


# ---------------------------------------------------------------------------
# get_notifications()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_notifications_returns_normalised_dicts(client: GitHubClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=_mock_response(_NOTIFICATIONS_RESPONSE))

    result = await client.get_notifications()

    assert len(result) == 2
    assert result[0]["id"] == "1001"
    assert result[0]["reason"] == "mention"
    assert result[0]["unread"] is True
    assert result[0]["title"] == "Fix parser bug"
    assert result[0]["type"] == "PullRequest"
    assert result[0]["repo"] == "tasin/tars"
    assert result[0]["updated_at"] == "2026-03-10T08:00:00Z"


@pytest.mark.asyncio
async def test_get_notifications_unread_only_default(client: GitHubClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=_mock_response([]))

    await client.get_notifications()

    call_kwargs = client._http.get.call_args
    params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))
    assert params["all"] == "false"


@pytest.mark.asyncio
async def test_get_notifications_all(client: GitHubClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=_mock_response([]))

    await client.get_notifications(unread_only=False)

    call_kwargs = client._http.get.call_args
    params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))
    assert params["all"] == "true"


# ---------------------------------------------------------------------------
# get_prs()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_prs_returns_normalised_dicts(client: GitHubClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=_mock_response(_PRS_RESPONSE))

    result = await client.get_prs("tasin/tars")

    assert len(result) == 2
    assert result[0]["number"] == 42
    assert result[0]["title"] == "Fix parser bug"
    assert result[0]["author"] == "tasin"
    assert result[0]["draft"] is False
    assert result[0]["labels"] == ["bug", "priority"]
    assert result[0]["url"] == "https://github.com/tasin/tars/pull/42"


@pytest.mark.asyncio
async def test_get_prs_draft_flag(client: GitHubClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=_mock_response(_PRS_RESPONSE))

    result = await client.get_prs("tasin/tars")

    assert result[1]["draft"] is True


@pytest.mark.asyncio
async def test_get_prs_passes_state_param(client: GitHubClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=_mock_response([]))

    await client.get_prs("tasin/tars", state="closed")

    call_kwargs = client._http.get.call_args
    params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))
    assert params["state"] == "closed"


# ---------------------------------------------------------------------------
# get_issues()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_issues_filters_out_pull_requests(client: GitHubClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=_mock_response(_ISSUES_RESPONSE))

    result = await client.get_issues("tasin/tars")

    # PR #42 should be filtered out, leaving issues #7 and #10
    assert len(result) == 2
    numbers = [i["number"] for i in result]
    assert 42 not in numbers
    assert 7 in numbers
    assert 10 in numbers


@pytest.mark.asyncio
async def test_get_issues_returns_normalised_dicts(client: GitHubClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=_mock_response(_ISSUES_RESPONSE))

    result = await client.get_issues("tasin/tars")

    issue = result[0]
    assert issue["number"] == 7
    assert issue["title"] == "Add CI workflow"
    assert issue["author"] == "tasin"
    assert issue["labels"] == ["enhancement"]
    assert issue["url"] == "https://github.com/tasin/tars/issues/7"


@pytest.mark.asyncio
async def test_get_issues_passes_state_param(client: GitHubClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=_mock_response([]))

    await client.get_issues("tasin/tars", state="all")

    call_kwargs = client._http.get.call_args
    params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))
    assert params["state"] == "all"


# ---------------------------------------------------------------------------
# mark_notification_read()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_notification_read_calls_patch(client: GitHubClient):
    patch_resp = httpx.Response(
        status_code=205,
        request=httpx.Request("PATCH", "https://fake"),
    )
    client._http = AsyncMock()
    client._http.patch = AsyncMock(return_value=patch_resp)

    await client.mark_notification_read("1001")

    client._http.patch.assert_awaited_once()
    call_args = client._http.patch.call_args
    assert "/notifications/threads/1001" in str(call_args)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_error_raises(client: GitHubClient):
    error_resp = _mock_response({"message": "Bad credentials"}, status_code=401)
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=error_resp)

    with pytest.raises(httpx.HTTPStatusError):
        await client.get_notifications()


@pytest.mark.asyncio
async def test_network_error_raises(client: GitHubClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    with pytest.raises(httpx.RequestError):
        await client.get_prs("tasin/tars")


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_shuts_down_http_client(client: GitHubClient):
    client._http = AsyncMock()
    client._http.aclose = AsyncMock()

    await client.close()

    client._http.aclose.assert_awaited_once()
