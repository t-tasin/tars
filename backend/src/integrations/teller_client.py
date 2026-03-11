"""Read-only Teller.io client for bank transaction data.

HC-04: T.A.R.S. may NEVER initiate financial transactions, make purchases,
move money, or modify any financial account. This client uses ONLY read
endpoints (GET /accounts, GET /accounts/:id/balances, GET /accounts/:id/transactions).

Authentication: mTLS (mutual TLS) with client certificate + private key for
development/production environments. Sandbox skips mTLS but still requires
an access token via HTTP Basic Auth.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from utils.errors import IntegrationError
from utils.resilience import get_service_health_registry

if TYPE_CHECKING:
    from db.models import Transaction

log = structlog.get_logger()

BASE_URL = "https://api.teller.io"


class TellerClient:
    """Read-only Teller.io client for bank transaction data.

    HC-04 compliance: This client NEVER initiates financial transactions,
    makes purchases, moves money, or modifies any financial account.
    Only GET endpoints are used.
    """

    def __init__(
        self,
        access_token: str,
        cert_path: str,
        key_path: str,
        env: str = "sandbox",
    ) -> None:
        self._access_token = access_token
        self._cert_path = cert_path
        self._key_path = key_path
        self._env = env
        self._client: httpx.AsyncClient | None = None
        self._cb = get_service_health_registry().get_or_create("teller")

    def _get_client(self) -> httpx.AsyncClient:
        """Return a cached httpx.AsyncClient with mTLS configured.

        Sandbox mode skips the client certificate (Teller sandbox doesn't
        require mTLS) but still uses the access token as Basic Auth.
        """
        if self._client is None:
            kwargs: dict[str, Any] = {
                "base_url": BASE_URL,
                "auth": httpx.BasicAuth(self._access_token, ""),
                "timeout": httpx.Timeout(30.0),
            }
            if self._env != "sandbox":
                kwargs["cert"] = (self._cert_path, self._key_path)

            self._client = httpx.AsyncClient(**kwargs)

        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Make an HTTP request with circuit breaker integration.

        Raises IntegrationError on HTTP errors. Records success/failure
        on the circuit breaker.
        """
        if self._cb.is_open:
            raise IntegrationError(
                "Teller circuit breaker is open",
                service="teller",
            )

        client = self._get_client()
        try:
            response = await client.request(method, path, params=params)
            response.raise_for_status()
            self._cb.record_success()
            return response.json()
        except httpx.HTTPStatusError as exc:
            self._cb.record_failure()
            raise IntegrationError(
                f"Teller API error: {exc.response.status_code} on {method} {path}",
                service="teller",
                details={"status_code": exc.response.status_code},
            ) from exc
        except httpx.HTTPError as exc:
            self._cb.record_failure()
            raise IntegrationError(
                f"Teller request failed: {exc}",
                service="teller",
            ) from exc

    # ------------------------------------------------------------------
    # Public API — READ ONLY (HC-04)
    # ------------------------------------------------------------------

    async def get_accounts(self) -> list[dict[str, Any]]:
        """List all linked bank accounts. Read-only (HC-04)."""
        accounts: list[dict[str, Any]] = await self._request("GET", "/accounts")
        log.info("teller_accounts_fetched", count=len(accounts))
        return accounts

    async def get_balances(self, account_id: str) -> dict[str, Any]:
        """Get balance for a single account. Read-only (HC-04)."""
        balances: dict[str, Any] = await self._request(
            "GET", f"/accounts/{account_id}/balances",
        )
        return balances

    async def get_transactions(
        self,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        """Fetch transactions across all accounts for a date range.

        Paginates through all accounts and all pages. Normalizes Teller's
        amount sign convention (negative = spend) to the codebase convention
        (positive = spend, negative = income).

        Teller only supports ``from_date`` filtering server-side, so
        ``end_date`` is applied client-side.
        """
        accounts = await self.get_accounts()
        all_transactions: list[dict[str, Any]] = []

        for account in accounts:
            account_id = account["id"]
            account_name = account.get("name")
            page_transactions = await self._fetch_account_transactions(
                account_id=account_id,
                account_name=account_name,
                start_date=start_date,
                end_date=end_date,
            )
            all_transactions.extend(page_transactions)

        log.info(
            "teller_transactions_fetched",
            count=len(all_transactions),
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            accounts=len(accounts),
        )
        return all_transactions

    async def sync_daily(self) -> int:
        """Sync yesterday's transactions into the transactions table.

        Fetches transactions for yesterday across all accounts, then inserts
        into the ``transactions`` table using INSERT ... ON CONFLICT DO NOTHING
        to deduplicate by ``teller_transaction_id``.

        Returns the count of new transactions inserted.

        HC-04: Read-only — fetches data and stores locally. No financial
        modifications are ever made.
        """
        from db.models import Transaction
        from db.session import get_db_session
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        yesterday = date.today() - timedelta(days=1)
        transactions = await self.get_transactions(yesterday, yesterday)

        if not transactions:
            log.info("teller_sync_no_new_transactions", date=yesterday.isoformat())
            return 0

        rows = [
            {
                "teller_transaction_id": txn["teller_transaction_id"],
                "teller_account_id": txn["teller_account_id"],
                "account_name": txn.get("account_name"),
                "merchant_name": txn.get("merchant_name"),
                "amount": txn["amount"],
                "currency": txn.get("currency", "USD"),
                "category": txn.get("category"),
                "subcategory": txn.get("subcategory"),
                "is_recurring": txn.get("is_recurring", False),
                "transaction_date": txn["transaction_date"],
                "pending": txn.get("pending", False),
                "metadata": txn.get("metadata", {}),
            }
            for txn in transactions
        ]

        async with get_db_session() as session:
            stmt = (
                pg_insert(Transaction)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["teller_transaction_id"])
                .returning(Transaction.id)
            )
            result = await session.execute(stmt)
            new_count = len(result.fetchall())

        log.info(
            "teller_sync_completed",
            date=yesterday.isoformat(),
            new_transactions=new_count,
            total_fetched=len(transactions),
        )
        return new_count

    async def close(self) -> None:
        """Close the underlying httpx client and release the connection pool."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    _MAX_PAGES_PER_ACCOUNT = 40  # 40 * 250 = 10,000 txns safety cap

    async def _fetch_account_transactions(
        self,
        *,
        account_id: str,
        account_name: str | None,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        """Paginate through all transactions for a single account."""
        transactions: list[dict[str, Any]] = []
        from_id: str | None = None

        for page_num in range(1, self._MAX_PAGES_PER_ACCOUNT + 1):
            params: dict[str, Any] = {
                "count": 250,
                "from_date": start_date.isoformat(),
            }
            if from_id is not None:
                params["from_id"] = from_id

            page: list[dict[str, Any]] = await self._request(
                "GET",
                f"/accounts/{account_id}/transactions",
                params=params,
            )

            if not page:
                break

            for raw_txn in page:
                txn_date = date.fromisoformat(raw_txn["date"])
                if txn_date > end_date:
                    continue
                transactions.append(
                    _normalize_transaction(raw_txn, account_name=account_name),
                )

            # If we got fewer than requested, no more pages
            if len(page) < 250:
                break

            from_id = page[-1]["id"]
        else:
            log.warning(
                "teller_pagination_limit_reached",
                account_id=account_id,
                pages=self._MAX_PAGES_PER_ACCOUNT,
            )

        return transactions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_transaction(
    raw: dict[str, Any],
    *,
    account_name: str | None = None,
) -> dict[str, Any]:
    """Normalize a Teller transaction dict to the codebase convention.

    CRITICAL: Teller uses the opposite sign convention from what the
    codebase expects:
      - Teller: negative = debit/spend, positive = credit/income
      - Codebase: positive = spend, negative = income

    We negate the amount to match the codebase convention.
    """
    normalized_amount = -Decimal(raw["amount"])  # Negate: Teller neg→pos (spend)

    details = raw.get("details") or {}
    counterparty = details.get("counterparty") or {}

    return {
        "teller_transaction_id": raw["id"],
        "teller_account_id": raw["account_id"],
        "account_name": account_name,
        "merchant_name": counterparty.get("name") or raw.get("description"),
        "amount": normalized_amount,
        "currency": "USD",  # Teller currently supports USD only
        "category": details.get("category"),
        "subcategory": None,  # Teller has single-level categories
        "is_recurring": False,  # Teller doesn't provide recurrence data
        "transaction_date": date.fromisoformat(raw["date"]),
        "pending": raw.get("status") == "pending",
        "metadata": {
            "description": raw.get("description"),
            "type": raw.get("type"),
            "running_balance": raw.get("running_balance"),
            "processing_status": details.get("processing_status"),
            "counterparty_type": counterparty.get("type"),
        },
    }
