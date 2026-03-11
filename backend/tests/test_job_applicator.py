"""Tests for the JobApplicator class and classify_apply_method helper."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.job_applicator import JobApplicator, classify_apply_method


# ---------------------------------------------------------------------------
# classify_apply_method
# ---------------------------------------------------------------------------


class TestClassifyApplyMethod:
    """Verify URL/source-based classification returns the correct method."""

    def test_classify_greenhouse_by_url(self) -> None:
        job: dict[str, Any] = {"url": "https://boards.greenhouse.io/acme/jobs/123"}
        assert classify_apply_method(job) == "api_greenhouse"

    def test_classify_greenhouse_by_source(self) -> None:
        job: dict[str, Any] = {"url": "https://example.com/apply", "source": "greenhouse"}
        assert classify_apply_method(job) == "api_greenhouse"

    def test_classify_lever(self) -> None:
        job: dict[str, Any] = {"url": "https://jobs.lever.co/acme/abc-123"}
        assert classify_apply_method(job) == "api_lever"

    def test_classify_ashby(self) -> None:
        job: dict[str, Any] = {"url": "https://jobs.ashbyhq.com/acme/opening-456"}
        assert classify_apply_method(job) == "api_ashby"

    def test_classify_email(self) -> None:
        job: dict[str, Any] = {"url": "https://example.com", "apply_email": "hr@acme.com"}
        assert classify_apply_method(job) == "email"

    def test_classify_manual_default(self) -> None:
        job: dict[str, Any] = {"url": "https://example.com/careers"}
        assert classify_apply_method(job) == "manual"


# ---------------------------------------------------------------------------
# JobApplicator.apply — Greenhouse
# ---------------------------------------------------------------------------


class TestApplyGreenhouse:
    """Test the Greenhouse ATS submission path."""

    @pytest.mark.asyncio
    async def test_apply_greenhouse_success(self) -> None:
        """Successful Greenhouse submission returns success=True."""
        applicator = JobApplicator()

        job: dict[str, Any] = {
            "url": "https://boards.greenhouse.io/acme/jobs/123",
            "external_id": "gh-acme-123",
            "company": "Acme",
            "title": "SWE Intern",
        }
        application: dict[str, Any] = {
            "qa_bank": {
                "full_name": "Tasin Test",
                "email": "tasin@example.com",
                "phone": "555-0100",
                "location": "Wooster, OH",
            },
            "cover_letter": "I am very interested.",
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "OK"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("integrations.job_applicator.httpx.AsyncClient", return_value=mock_client):
            result = await applicator.apply(job, application, "api_greenhouse")

        assert result["success"] is True
        assert "Greenhouse" in result["message"]
        mock_client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_apply_greenhouse_failure(self) -> None:
        """HTTP 400 from Greenhouse results in success=False."""
        applicator = JobApplicator()

        job: dict[str, Any] = {
            "url": "https://boards.greenhouse.io/acme/jobs/123",
            "external_id": "gh-acme-123",
            "company": "Acme",
            "title": "SWE Intern",
        }
        application: dict[str, Any] = {"qa_bank": {"full_name": "Test User", "email": "t@e.com"}}

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request: missing required field"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("integrations.job_applicator.httpx.AsyncClient", return_value=mock_client):
            result = await applicator.apply(job, application, "api_greenhouse")

        assert result["success"] is False
        assert "400" in result["message"]


# ---------------------------------------------------------------------------
# JobApplicator.apply — Lever / Ashby stubs
# ---------------------------------------------------------------------------


class TestApplyStubs:
    """Lever and Ashby are not yet implemented and should return failure."""

    @pytest.mark.asyncio
    async def test_apply_lever_not_implemented(self) -> None:
        applicator = JobApplicator()
        job: dict[str, Any] = {"url": "https://jobs.lever.co/acme/abc", "company": "Acme", "title": "SWE"}
        result = await applicator.apply(job, {}, "api_lever")

        assert result["success"] is False
        assert "not yet implemented" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_apply_ashby_not_implemented(self) -> None:
        applicator = JobApplicator()
        job: dict[str, Any] = {"url": "https://jobs.ashbyhq.com/acme/xyz", "company": "Acme", "title": "SWE"}
        result = await applicator.apply(job, {}, "api_ashby")

        assert result["success"] is False
        assert "not yet implemented" in result["message"].lower()


# ---------------------------------------------------------------------------
# JobApplicator.apply — Email
# ---------------------------------------------------------------------------


class TestApplyEmail:
    """Test the email-based application path."""

    @pytest.mark.asyncio
    async def test_apply_email_sends_with_attachment(self) -> None:
        """Email application calls send_email_with_attachment on the gmail client."""
        mock_gmail = AsyncMock()
        mock_gmail.send_email_with_attachment = AsyncMock(return_value={"message_id": "abc"})

        applicator = JobApplicator(gmail_client=mock_gmail)

        job: dict[str, Any] = {
            "company": "Acme",
            "title": "SWE Intern",
            "apply_email": "jobs@acme.com",
        }
        application: dict[str, Any] = {
            "qa_bank": {"full_name": "Tasin Test", "email": "tasin@test.com"},
            "cover_letter": "I am interested in this role.",
            "resume_path": "/tmp/fake_resume.pdf",
        }

        # Patch Path.exists to return True so the attachment branch is taken
        with patch("integrations.job_applicator.Path.exists", return_value=True):
            result = await applicator.apply(job, application, "email")

        assert result["success"] is True
        assert "jobs@acme.com" in result["message"]
        mock_gmail.send_email_with_attachment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_apply_email_no_gmail(self) -> None:
        """Without a gmail client the email path should fail gracefully."""
        applicator = JobApplicator(gmail_client=None)

        job: dict[str, Any] = {
            "company": "Acme",
            "title": "SWE Intern",
            "apply_email": "jobs@acme.com",
        }

        result = await applicator.apply(job, {}, "email")

        assert result["success"] is False
        assert "not available" in result["message"].lower()
