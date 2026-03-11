"""Tests for TellerClient — read-only bank transaction fetching (HC-04)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from integrations.teller_client import TellerClient, _normalize_transaction


# ---------------------------------------------------------------------------
# Test data builders
# ---------------------------------------------------------------------------


def _make_teller_transaction(
    txn_id: str = "txn_001",
    account_id: str = "acc_001",
    amount: str = "-12.50",
    txn_date: str = "2026-03-10",
    description: str = "STARBUCKS #1234",
    status: str = "posted",
    txn_type: str = "card_payment",
    category: str = "dining",
    counterparty_name: str = "Starbucks",
) -> dict[str, Any]:
    return {
        "id": txn_id,
        "account_id": account_id,
        "amount": amount,
        "date": txn_date,
        "description": description,
        "status": status,
        "type": txn_type,
        "running_balance": "4837.50",
        "details": {
            "processing_status": "complete",
            "category": category,
            "counterparty": {"name": counterparty_name, "type": "organization"},
        },
        "links": {"self": f"https://api.teller.io/accounts/{account_id}/transactions/{txn_id}"},
    }


def _make_teller_account(
    account_id: str = "acc_001",
    name: str = "Checking",
    acct_type: str = "depository",
    subtype: str = "checking",
    institution: str = "Chase",
) -> dict[str, Any]:
    return {
        "id": account_id,
        "name": name,
        "type": acct_type,
        "subtype": subtype,
        "institution": {"name": institution, "id": institution.lower()},
        "currency": "USD",
        "enrollment_id": "enr_001",
        "status": "open",
        "links": {},
    }


def _mock_response(json_data: Any, status_code: int = 200) -> httpx.Response:
    """Build a mock httpx.Response."""
    response = httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "https://api.teller.io/test"),
    )
    return response


# ---------------------------------------------------------------------------
# Tests: _normalize_transaction
# ---------------------------------------------------------------------------


class TestNormalizeTransaction:
    """Test the _normalize_transaction helper."""

    def test_normalizes_fields(self) -> None:
        raw = _make_teller_transaction()
        result = _normalize_transaction(raw, account_name="Checking")

        assert result["teller_transaction_id"] == "txn_001"
        assert result["teller_account_id"] == "acc_001"
        assert result["account_name"] == "Checking"
        assert result["merchant_name"] == "Starbucks"
        assert result["category"] == "dining"
        assert result["pending"] is False
        assert result["transaction_date"] == date(2026, 3, 10)

    def test_amount_sign_normalization_spend(self) -> None:
        """Teller negative (spend) should become positive in our convention."""
        raw = _make_teller_transaction(amount="-12.50")
        result = _normalize_transaction(raw)
        assert result["amount"] == Decimal("12.50")

    def test_amount_sign_normalization_income(self) -> None:
        """Teller positive (income) should become negative in our convention."""
        raw = _make_teller_transaction(amount="500.00")
        result = _normalize_transaction(raw)
        assert result["amount"] == Decimal("-500.00")

    def test_falls_back_to_description_when_no_counterparty(self) -> None:
        raw = _make_teller_transaction(description="DIRECT DEBIT #9876")
        raw["details"]["counterparty"] = {}
        result = _normalize_transaction(raw)
        assert result["merchant_name"] == "DIRECT DEBIT #9876"

    def test_pending_status(self) -> None:
        raw = _make_teller_transaction(status="pending")
        result = _normalize_transaction(raw)
        assert result["pending"] is True

    def test_metadata_contains_teller_fields(self) -> None:
        raw = _make_teller_transaction()
        result = _normalize_transaction(raw)
        meta = result["metadata"]
        assert meta["description"] == "STARBUCKS #1234"
        assert meta["type"] == "card_payment"
        assert meta["running_balance"] == "4837.50"
        assert meta["processing_status"] == "complete"


# ---------------------------------------------------------------------------
# Tests: TellerClient construction and mTLS
# ---------------------------------------------------------------------------


class TestTellerClientInit:
    """Test TellerClient construction and mTLS configuration."""

    def test_sandbox_skips_cert(self) -> None:
        """In sandbox mode, httpx.AsyncClient should be created without cert kwarg."""
        with patch("integrations.teller_client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = TellerClient(
                access_token="test-token",
                cert_path="/path/cert.pem",
                key_path="/path/key.pem",
                env="sandbox",
            )
            client._get_client()
            call_kwargs = mock_cls.call_args[1]
            assert "cert" not in call_kwargs

    def test_production_uses_cert(self) -> None:
        """In production mode, httpx.AsyncClient should be created with cert kwarg."""
        with patch("integrations.teller_client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = TellerClient(
                access_token="test-token",
                cert_path="/path/cert.pem",
                key_path="/path/key.pem",
                env="production",
            )
            client._get_client()
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["cert"] == ("/path/cert.pem", "/path/key.pem")

    @pytest.mark.asyncio
    async def test_close_cleans_up(self) -> None:
        client = TellerClient(
            access_token="test-token",
            cert_path="/path/cert.pem",
            key_path="/path/key.pem",
            env="sandbox",
        )
        _ = client._get_client()
        assert client._client is not None
        await client.close()
        assert client._client is None


# ---------------------------------------------------------------------------
# Tests: get_accounts
# ---------------------------------------------------------------------------


class TestTellerClientGetAccounts:
    """Test TellerClient.get_accounts (read-only)."""

    @pytest.mark.asyncio
    async def test_returns_account_list(self) -> None:
        client = TellerClient(
            access_token="test-token",
            cert_path="",
            key_path="",
            env="sandbox",
        )
        accounts_data = [
            _make_teller_account(account_id="acc_001", name="Checking"),
            _make_teller_account(account_id="acc_002", name="Savings"),
        ]

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_mock_response(accounts_data))
        client._client = mock_http

        accounts = await client.get_accounts()

        assert len(accounts) == 2
        assert accounts[0]["name"] == "Checking"
        assert accounts[1]["name"] == "Savings"
        mock_http.request.assert_called_once_with(
            "GET", "/accounts", params=None,
        )


# ---------------------------------------------------------------------------
# Tests: get_balances
# ---------------------------------------------------------------------------


class TestTellerClientGetBalances:
    """Test TellerClient.get_balances (read-only)."""

    @pytest.mark.asyncio
    async def test_returns_balance(self) -> None:
        client = TellerClient(
            access_token="test-token",
            cert_path="",
            key_path="",
            env="sandbox",
        )
        balance_data = {
            "account_id": "acc_001",
            "ledger": "5000.00",
            "available": "4850.00",
            "links": {},
        }

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_mock_response(balance_data))
        client._client = mock_http

        result = await client.get_balances("acc_001")

        assert result["ledger"] == "5000.00"
        assert result["available"] == "4850.00"


# ---------------------------------------------------------------------------
# Tests: get_transactions (with pagination)
# ---------------------------------------------------------------------------


class TestTellerClientGetTransactions:
    """Test TellerClient.get_transactions with pagination and filtering."""

    @pytest.mark.asyncio
    async def test_fetches_and_normalizes(self) -> None:
        client = TellerClient(
            access_token="test-token",
            cert_path="",
            key_path="",
            env="sandbox",
        )

        accounts_data = [_make_teller_account()]
        txn_data = [
            _make_teller_transaction(txn_id="txn_001", amount="-5.50"),
            _make_teller_transaction(txn_id="txn_002", amount="-12.00"),
        ]

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(
            side_effect=[
                _mock_response(accounts_data),  # get_accounts
                _mock_response(txn_data),        # transactions page 1
            ],
        )
        client._client = mock_http

        result = await client.get_transactions(
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 31),
        )

        assert len(result) == 2
        # Amounts should be negated (spend → positive)
        assert result[0]["amount"] == Decimal("5.50")
        assert result[1]["amount"] == Decimal("12.00")

    @pytest.mark.asyncio
    async def test_filters_by_end_date(self) -> None:
        """Transactions after end_date should be excluded (client-side)."""
        client = TellerClient(
            access_token="test-token",
            cert_path="",
            key_path="",
            env="sandbox",
        )

        accounts_data = [_make_teller_account()]
        txn_data = [
            _make_teller_transaction(txn_id="txn_in", txn_date="2026-03-10"),
            _make_teller_transaction(txn_id="txn_out", txn_date="2026-03-20"),
        ]

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(
            side_effect=[
                _mock_response(accounts_data),
                _mock_response(txn_data),
            ],
        )
        client._client = mock_http

        result = await client.get_transactions(
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 15),
        )

        assert len(result) == 1
        assert result[0]["teller_transaction_id"] == "txn_in"

    @pytest.mark.asyncio
    async def test_pagination_multiple_pages(self) -> None:
        """When a page returns 250 items, fetch the next page."""
        client = TellerClient(
            access_token="test-token",
            cert_path="",
            key_path="",
            env="sandbox",
        )

        accounts_data = [_make_teller_account()]

        # Page 1: exactly 250 transactions
        page1 = [
            _make_teller_transaction(txn_id=f"txn_{i:04d}")
            for i in range(250)
        ]
        # Page 2: 10 transactions (final page)
        page2 = [
            _make_teller_transaction(txn_id=f"txn_{i:04d}")
            for i in range(250, 260)
        ]

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(
            side_effect=[
                _mock_response(accounts_data),  # get_accounts
                _mock_response(page1),           # page 1
                _mock_response(page2),           # page 2
            ],
        )
        client._client = mock_http

        result = await client.get_transactions(
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 31),
        )

        assert len(result) == 260

        # Verify page 2 was called with from_id of last item in page 1
        calls = mock_http.request.call_args_list
        assert calls[2][1]["params"]["from_id"] == "txn_0249"

    @pytest.mark.asyncio
    async def test_multiple_accounts(self) -> None:
        """Transactions are fetched from all linked accounts."""
        client = TellerClient(
            access_token="test-token",
            cert_path="",
            key_path="",
            env="sandbox",
        )

        accounts_data = [
            _make_teller_account(account_id="acc_001", name="Checking"),
            _make_teller_account(account_id="acc_002", name="Savings"),
        ]
        txns_checking = [_make_teller_transaction(txn_id="txn_c1", account_id="acc_001")]
        txns_savings = [_make_teller_transaction(txn_id="txn_s1", account_id="acc_002")]

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(
            side_effect=[
                _mock_response(accounts_data),
                _mock_response(txns_checking),
                _mock_response(txns_savings),
            ],
        )
        client._client = mock_http

        result = await client.get_transactions(
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 31),
        )

        assert len(result) == 2
        ids = {t["teller_transaction_id"] for t in result}
        assert ids == {"txn_c1", "txn_s1"}


# ---------------------------------------------------------------------------
# Tests: sync_daily
# ---------------------------------------------------------------------------


class TestTellerClientSyncDaily:
    """Test TellerClient.sync_daily with dedup via ON CONFLICT."""

    @pytest.mark.asyncio
    async def test_sync_inserts_with_on_conflict(self) -> None:
        client = TellerClient(
            access_token="test-token",
            cert_path="",
            key_path="",
            env="sandbox",
        )

        accounts_data = [_make_teller_account()]
        txn_data = [_make_teller_transaction(txn_id="txn_daily_001")]

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(
            side_effect=[
                _mock_response(accounts_data),
                _mock_response(txn_data),
            ],
        )
        client._client = mock_http

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("fake-uuid",)]  # 1 row inserted
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("db.session.get_db_session") as mock_get_db, \
             patch("sqlalchemy.dialects.postgresql.insert") as mock_pg_insert:

            # Make get_db_session an async context manager yielding our mock session
            mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_stmt = MagicMock()
            mock_stmt.on_conflict_do_nothing.return_value = mock_stmt
            mock_stmt.returning.return_value = mock_stmt
            mock_pg_insert.return_value.values.return_value = mock_stmt

            new_count = await client.sync_daily()

        assert new_count == 1
        mock_pg_insert.return_value.values.assert_called_once()
        mock_stmt.on_conflict_do_nothing.assert_called_once_with(
            index_elements=["teller_transaction_id"],
        )

    @pytest.mark.asyncio
    async def test_sync_returns_zero_when_no_transactions(self) -> None:
        client = TellerClient(
            access_token="test-token",
            cert_path="",
            key_path="",
            env="sandbox",
        )

        accounts_data = [_make_teller_account()]

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(
            side_effect=[
                _mock_response(accounts_data),
                _mock_response([]),  # no transactions
            ],
        )
        client._client = mock_http

        new_count = await client.sync_daily()
        assert new_count == 0


# ---------------------------------------------------------------------------
# Tests: error handling and circuit breaker
# ---------------------------------------------------------------------------


class TestTellerClientErrorHandling:
    """Test error handling and circuit breaker integration."""

    @staticmethod
    def _make_client_with_fresh_cb() -> TellerClient:
        """Create a TellerClient with a fresh (isolated) circuit breaker."""
        from utils.resilience import CircuitBreakerState

        client = TellerClient(
            access_token="test-token",
            cert_path="",
            key_path="",
            env="sandbox",
        )
        # Replace shared singleton CB with a fresh instance for test isolation
        client._cb = CircuitBreakerState(service="teller_test")
        return client

    @pytest.mark.asyncio
    async def test_http_error_raises_integration_error(self) -> None:
        client = self._make_client_with_fresh_cb()

        error_response = httpx.Response(
            status_code=401,
            json={"error": "unauthorized"},
            request=httpx.Request("GET", "https://api.teller.io/accounts"),
        )
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=error_response)
        client._client = mock_http

        from utils.errors import IntegrationError
        with pytest.raises(IntegrationError, match="401"):
            await client.get_accounts()

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_when_open(self) -> None:
        client = self._make_client_with_fresh_cb()

        # Force the circuit breaker open
        for _ in range(5):
            client._cb.record_failure()
        assert client._cb.is_open

        from utils.errors import IntegrationError
        with pytest.raises(IntegrationError, match="circuit breaker"):
            await client.get_accounts()

    @pytest.mark.asyncio
    async def test_records_failure_on_http_error(self) -> None:
        client = self._make_client_with_fresh_cb()

        initial_failures = client._cb.failure_count

        error_response = httpx.Response(
            status_code=500,
            json={"error": "internal"},
            request=httpx.Request("GET", "https://api.teller.io/accounts"),
        )
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=error_response)
        client._client = mock_http

        from utils.errors import IntegrationError
        with pytest.raises(IntegrationError):
            await client.get_accounts()

        assert client._cb.failure_count == initial_failures + 1

    @pytest.mark.asyncio
    async def test_records_success_on_ok(self) -> None:
        client = self._make_client_with_fresh_cb()

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_mock_response([]))
        client._client = mock_http

        await client.get_accounts()
        assert client._cb.last_success is not None


# ---------------------------------------------------------------------------
# Tests: HC-04 compliance
# ---------------------------------------------------------------------------


class TestHC04Compliance:
    """Verify HC-04: no write operations exist in TellerClient."""

    def test_no_write_methods_in_source(self) -> None:
        """Ensure TellerClient only uses GET requests (read-only)."""
        import inspect
        from integrations import teller_client

        source = inspect.getsource(teller_client)

        # Teller write endpoints that must NEVER appear
        forbidden = [
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "/accounts/create",
            "/transfers",
            "/payments",
        ]
        for endpoint in forbidden:
            # Only check non-test, non-comment, non-docstring references
            # Filter out our own test method names and string literals
            lines = [
                line for line in source.split("\n")
                if endpoint in line
                and not line.strip().startswith("#")
                and not line.strip().startswith('"')
                and not line.strip().startswith("'")
            ]
            assert len(lines) == 0, (
                f"HC-04 VIOLATION: Found '{endpoint}' in teller_client.py "
                f"in non-comment context. TellerClient must be strictly read-only."
            )
