"""Tests for Telegram job digest formatting and delivery."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from integrations.telegram_job_handlers import (
    send_job_details_message,
    send_job_digest,
    send_jobs_status,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(**overrides: Any) -> dict[str, Any]:
    """Create a test job dict with sensible defaults."""
    base: dict[str, Any] = {
        "id": str(uuid4()),
        "title": "Software Engineer",
        "company": "Acme Corp",
        "location": "Remote",
        "salary_range": "$120k-$160k",
        "match_score": 85,
        "source": "greenhouse",
        "apply_method": "api_greenhouse",
    }
    base.update(overrides)
    return base


def _make_gateway() -> AsyncMock:
    """Return a mock TelegramGateway with send_message as an AsyncMock."""
    gw = AsyncMock()
    gw.send_message = AsyncMock()
    return gw


# ---------------------------------------------------------------------------
# send_job_digest
# ---------------------------------------------------------------------------


class TestSendJobDigest:
    """Test the daily job digest delivery via Telegram."""

    @pytest.mark.asyncio
    async def test_send_digest_empty(self) -> None:
        """No jobs should produce a 'No new matches' message."""
        gw = _make_gateway()

        # Patch the telegram import used inside send_job_digest
        mock_button = MagicMock()
        mock_markup = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "telegram": MagicMock(
                    InlineKeyboardButton=mock_button,
                    InlineKeyboardMarkup=mock_markup,
                )
            },
        ):
            await send_job_digest(gw, [])

        gw.send_message.assert_awaited_once()
        call_text = gw.send_message.call_args.kwargs.get("text", "")
        assert "No new matches" in call_text

    @pytest.mark.asyncio
    async def test_send_digest_formats_correctly(self) -> None:
        """3 jobs should produce 1 header message + 3 individual job messages."""
        gw = _make_gateway()
        jobs = [_make_job(title=f"Role {i}") for i in range(3)]

        mock_telegram = MagicMock()
        mock_telegram.InlineKeyboardButton = MagicMock()
        mock_telegram.InlineKeyboardMarkup = MagicMock()
        with patch.dict("sys.modules", {"telegram": mock_telegram}):
            await send_job_digest(gw, jobs)

        # 1 header + 3 job messages = 4 total calls
        assert gw.send_message.await_count == 4

    @pytest.mark.asyncio
    async def test_send_digest_max_15(self) -> None:
        """Even with 20 jobs, only 15 individual messages are sent (plus header)."""
        gw = _make_gateway()
        jobs = [_make_job(title=f"Role {i}") for i in range(20)]

        mock_telegram = MagicMock()
        mock_telegram.InlineKeyboardButton = MagicMock()
        mock_telegram.InlineKeyboardMarkup = MagicMock()
        with patch.dict("sys.modules", {"telegram": mock_telegram}):
            await send_job_digest(gw, jobs)

        # 1 header + 15 job messages = 16 total calls
        assert gw.send_message.await_count == 16


# ---------------------------------------------------------------------------
# send_jobs_status
# ---------------------------------------------------------------------------


class TestSendJobsStatus:
    """Test the /jobs status summary message."""

    @pytest.mark.asyncio
    async def test_send_jobs_status(self) -> None:
        """Verify the status counts are formatted and sent."""
        import sys
        from contextlib import asynccontextmanager

        gw = _make_gateway()

        # The function queries the DB via get_db_session; we mock that.
        mock_result = MagicMock()
        mock_result.all.return_value = [
            ("new", 5),
            ("applied", 3),
            ("skipped", 2),
        ]

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        @asynccontextmanager
        async def fake_get_db_session():
            yield mock_session

        # Patch the fake db.session module that conftest installed
        db_session_mod = sys.modules["db.session"]
        original = db_session_mod.get_db_session
        db_session_mod.get_db_session = fake_get_db_session
        try:
            await send_jobs_status(gw)
        finally:
            db_session_mod.get_db_session = original

        gw.send_message.assert_awaited_once()
        text = gw.send_message.call_args.kwargs.get("text", "")
        assert "Pipeline Status" in text
        assert "10" in text  # total = 5 + 3 + 2


# ---------------------------------------------------------------------------
# send_job_details_message
# ---------------------------------------------------------------------------


class TestSendJobDetailsMessage:
    """Test the detailed job evaluation message."""

    @pytest.mark.asyncio
    async def test_send_job_details_message(self) -> None:
        """Verify details formatting includes key fields."""
        gw = _make_gateway()

        details: dict[str, Any] = {
            "title": "ML Engineer",
            "company": "DeepMind",
            "location": "London",
            "salary_range": "$200k-$300k",
            "match_score": 92,
            "match_reasons": ["Strong ML background", "Python expertise"],
            "concerns": ["Requires UK work authorization"],
            "fit_analysis": "Excellent fit for your background.",
            "description": "We are looking for an ML engineer...",
            "url": "https://deepmind.com/careers/123",
            "apply_method": "manual",
        }

        await send_job_details_message(gw, details)

        gw.send_message.assert_awaited_once()
        text = gw.send_message.call_args.kwargs.get("text", "")

        assert "ML Engineer" in text
        assert "DeepMind" in text
        assert "92%" in text
        assert "Strong ML background" in text
        assert "UK work authorization" in text
        assert "deepmind.com" in text
