"""GitHub REST API client for T.A.R.S. (read-only, Phase 2)."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from utils.resilience import get_service_health_registry, retry_with_backoff

log = structlog.get_logger()

_BASE_URL = "https://api.github.com"
_SERVICE_NAME = "github"


class GitHubClient:
    """Async GitHub REST API client for notifications, PRs, and issues.

    Phase 2: read-only operations. PR creation comes in Phase 4 (coding agent).

    Includes retry with exponential backoff and circuit breaker.
    """

    def __init__(self, pat: str) -> None:
        self._http = httpx.AsyncClient(
            base_url=_BASE_URL,
            headers={
                "Authorization": f"token {pat}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15.0,
        )
        self._cb = get_service_health_registry().get_or_create(_SERVICE_NAME)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_notifications(
        self,
        unread_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch GitHub notifications.

        Returns a list of notification dicts with normalised fields::

            [{"id": "123", "reason": "mention", "unread": True,
              "title": "Fix bug in parser", "type": "PullRequest",
              "repo": "tasin/tars", "url": "https://...",
              "updated_at": "2026-03-10T08:00:00Z"}, ...]
        """
        params: dict[str, Any] = {"all": "false" if unread_only else "true"}
        data = await self._get("/notifications", params=params)

        return [
            {
                "id": n["id"],
                "reason": n.get("reason", ""),
                "unread": n.get("unread", False),
                "title": n.get("subject", {}).get("title", ""),
                "type": n.get("subject", {}).get("type", ""),
                "repo": n.get("repository", {}).get("full_name", ""),
                "url": n.get("subject", {}).get("url", ""),
                "updated_at": n.get("updated_at", ""),
            }
            for n in data
        ]

    async def get_prs(
        self,
        repo: str,
        state: str = "open",
    ) -> list[dict[str, Any]]:
        """List pull requests for a repository.

        Args:
            repo: Full repo name (e.g. ``"tasin/tars"``).
            state: ``"open"``, ``"closed"``, or ``"all"``.

        Returns a list of PR dicts::

            [{"number": 42, "title": "Add feature", "state": "open",
              "author": "tasin", "draft": False, "labels": ["enhancement"],
              "created_at": "...", "updated_at": "...", "url": "..."}, ...]
        """
        data = await self._get(
            f"/repos/{repo}/pulls",
            params={"state": state, "per_page": 30},
        )

        return [
            {
                "number": pr["number"],
                "title": pr.get("title", ""),
                "state": pr.get("state", ""),
                "author": pr.get("user", {}).get("login", ""),
                "draft": pr.get("draft", False),
                "labels": [label["name"] for label in pr.get("labels", [])],
                "created_at": pr.get("created_at", ""),
                "updated_at": pr.get("updated_at", ""),
                "url": pr.get("html_url", ""),
            }
            for pr in data
        ]

    async def get_issues(
        self,
        repo: str,
        state: str = "open",
    ) -> list[dict[str, Any]]:
        """List issues for a repository (excludes pull requests).

        Args:
            repo: Full repo name (e.g. ``"tasin/tars"``).
            state: ``"open"``, ``"closed"``, or ``"all"``.

        Returns a list of issue dicts::

            [{"number": 7, "title": "Bug report", "state": "open",
              "author": "tasin", "labels": ["bug"],
              "created_at": "...", "updated_at": "...", "url": "..."}, ...]
        """
        data = await self._get(
            f"/repos/{repo}/issues",
            params={"state": state, "per_page": 30},
        )

        # The issues endpoint also returns PRs; filter them out.
        return [
            {
                "number": issue["number"],
                "title": issue.get("title", ""),
                "state": issue.get("state", ""),
                "author": issue.get("user", {}).get("login", ""),
                "labels": [label["name"] for label in issue.get("labels", [])],
                "created_at": issue.get("created_at", ""),
                "updated_at": issue.get("updated_at", ""),
                "url": issue.get("html_url", ""),
            }
            for issue in data
            if "pull_request" not in issue
        ]

    async def mark_notification_read(self, notification_id: str) -> None:
        """Mark a single notification as read."""
        await self._patch(f"/notifications/threads/{notification_id}")
        log.info("github_notification_read", notification_id=notification_id)

    async def close(self) -> None:
        """Shut down the underlying HTTP client."""
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @retry_with_backoff(
        max_retries=3,
        backoff_seconds=(1.0, 2.0, 4.0),
        retryable_exceptions=(httpx.TimeoutException, httpx.ConnectError),
        service=_SERVICE_NAME,
    )
    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Issue a GET request and return parsed JSON.

        Retries on timeouts and connection errors with circuit breaker.
        """
        try:
            resp = await self._http.get(path, params=params)
            resp.raise_for_status()
            data: Any = resp.json()
            log.debug("github_api_ok", path=path, status=resp.status_code)
            return data
        except httpx.HTTPStatusError as exc:
            log.error(
                "github_api_error",
                path=path,
                status=exc.response.status_code,
                body=exc.response.text[:200],
            )
            raise
        except httpx.RequestError as exc:
            log.error("github_request_error", path=path, error=str(exc))
            raise

    @retry_with_backoff(
        max_retries=3,
        backoff_seconds=(1.0, 2.0, 4.0),
        retryable_exceptions=(httpx.TimeoutException, httpx.ConnectError),
        service=_SERVICE_NAME,
    )
    async def _patch(
        self,
        path: str,
        json: dict[str, Any] | None = None,
    ) -> None:
        """Issue a PATCH request (used for marking notifications read).

        Retries on timeouts and connection errors with circuit breaker.
        """
        try:
            resp = await self._http.patch(path, json=json)
            resp.raise_for_status()
            log.debug("github_api_ok", path=path, status=resp.status_code)
        except httpx.HTTPStatusError as exc:
            log.error(
                "github_api_error",
                path=path,
                status=exc.response.status_code,
                body=exc.response.text[:200],
            )
            raise
        except httpx.RequestError as exc:
            log.error("github_request_error", path=path, error=str(exc))
            raise
