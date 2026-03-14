# Node 1 Direct Deployment + Telegram Incoming + Multi-Agent Pipeline

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate Node 1 backend from Docker to direct deployment (enabling Claude Code CLI), wire up Telegram as a full interactive client, and build the multi-agent coding pipeline.

**Architecture:** Backend runs directly on Node 1 host via systemd, PostgreSQL + Cloudflared stay in Docker. Telegram bot uses python-telegram-bot 21.x `Application` with polling for incoming messages and callback queries. Multi-agent coding pipeline uses a planner → workers → reviewer → tester pattern with multiple Claude Code CLI instances.

**Tech Stack:** Python 3.12 (pyenv), systemd, python-telegram-bot 21.x Application, Claude Code CLI, MCP servers (npx), asyncio

---

## File Structure

### New Files
- `deploy/node1/tars-backend.service` — systemd unit file
- `deploy/scripts/setup-node1-direct.sh` — one-time host setup script
- `deploy/node1/docker-compose.yml` — updated (remove tars-backend service)
- `backend/src/integrations/telegram_handlers.py` — incoming message + callback handlers
- `backend/src/agents/coding_pipeline.py` — multi-agent planner/worker/reviewer orchestration
- `backend/tests/test_telegram_handlers.py` — tests for Telegram handler routing
- `backend/tests/test_coding_pipeline.py` — tests for multi-agent pipeline

### Modified Files
- `backend/src/main.py` — add Telegram Application startup/shutdown in lifespan
- `backend/src/integrations/telegram_bot.py` — expose bot instance for Application
- `backend/src/models/claude_spawner.py` — add streaming support for progress notifications
- `backend/src/agents/coding.py` — use local Claude instead of Redis dispatch to Node 2
- `backend/src/config.py` — add telegram_webhook_mode, coding pipeline settings

---

## Chunk 1: Node 1 Direct Deployment Migration

### Task 1: Create systemd service file

**Files:**
- Create: `deploy/node1/tars-backend.service`

- [ ] **Step 1: Write the systemd unit file**

```ini
[Unit]
Description=T.A.R.S. Backend API
After=network.target docker.service
Requires=docker.service

[Service]
Type=exec
User=tasin
Group=tasin
WorkingDirectory=/opt/tars/backend
EnvironmentFile=/opt/tars/deploy/node1/.env
ExecStart=/opt/tars/backend/.venv/bin/uvicorn src.main:app --host 0.0.0.0 --port 8000 --loop uvloop --workers 1
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=tars-backend

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/tars /data
PrivateTmp=true

# Audio device access for wake word
SupplementaryGroups=audio

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Commit**

```bash
git add deploy/node1/tars-backend.service
git commit -m "deploy: add systemd service for direct backend deployment"
```

---

### Task 2: Create host setup script

**Files:**
- Create: `deploy/scripts/setup-node1-direct.sh`

- [ ] **Step 1: Write the setup script**

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "=== T.A.R.S. Node 1 Direct Deployment Setup ==="

# 1. Install system dependencies
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    build-essential libpq-dev portaudio19-dev ffmpeg \
    libportaudio2 libpq5 \
    curl git

# 2. Install pyenv + Python 3.12
if ! command -v pyenv &>/dev/null; then
    curl -fsSL https://pyenv.run | bash
    echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.bashrc
    echo 'export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.bashrc
    echo 'eval "$(pyenv init -)"' >> ~/.bashrc
    export PYENV_ROOT="$HOME/.pyenv"
    export PATH="$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init -)"
fi
pyenv install -s 3.12
pyenv global 3.12

# 3. Install Node.js 22 LTS (for MCP servers)
if ! command -v node &>/dev/null || [[ "$(node -v)" != v22* ]]; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi

# 4. Install Claude Code CLI
if ! command -v claude &>/dev/null; then
    npm install -g @anthropic-ai/claude-code
    echo ">>> Run 'claude login' to authenticate with your Max plan <<<"
fi

# 5. Set up backend virtualenv
cd /opt/tars/backend
python -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install --extra-index-url https://download.pytorch.org/whl/cpu -e .

# 6. Create data directories
sudo mkdir -p /data/repos /data/outputs /data/logs
sudo chown -R tasin:tasin /data

# 7. Install systemd service
sudo cp /opt/tars/deploy/node1/tars-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tars-backend

echo ""
echo "=== Setup complete ==="
echo "Next steps:"
echo "  1. Run 'claude login' to authenticate Claude Code CLI"
echo "  2. Update docker-compose.yml to remove tars-backend service"
echo "  3. Run: sudo systemctl start tars-backend"
echo "  4. Check: sudo journalctl -u tars-backend -f"
```

- [ ] **Step 2: Make executable and commit**

```bash
chmod +x deploy/scripts/setup-node1-direct.sh
git add deploy/scripts/setup-node1-direct.sh
git commit -m "deploy: add Node 1 direct deployment setup script"
```

---

### Task 3: Update docker-compose to remove tars-backend

**Files:**
- Modify: `deploy/node1/docker-compose.yml`

- [ ] **Step 1: Remove the tars-backend service, keep tars-db + cloudflared**

The updated `docker-compose.yml` should contain only:

```yaml
services:
  tars-db:
    image: postgres:16-alpine
    container_name: tars-db
    restart: unless-stopped
    environment:
      POSTGRES_DB: tars
      POSTGRES_USER: tars
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U tars -d tars"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    deploy:
      resources:
        limits:
          memory: 2G
    networks:
      - tars-net

  cloudflared:
    image: cloudflare/cloudflared:latest
    container_name: cloudflared
    restart: unless-stopped
    command: tunnel run
    environment:
      TUNNEL_TOKEN: ${CLOUDFLARE_TUNNEL_TOKEN}
    network_mode: host

volumes:
  pgdata:
    driver: local

networks:
  tars-net:
    driver: bridge
```

Key changes:
- Removed `tars-backend` service entirely
- Bind PostgreSQL to `127.0.0.1:5432` (host-only, no external exposure)
- Changed cloudflared to `network_mode: host` so it can reach backend on localhost:8000
- Removed cloudflared dependency on tars-backend

- [ ] **Step 2: Update backend DATABASE_URL in `.env`**

On the server, update `.env` to point at localhost instead of Docker network:
```
DATABASE_URL=postgresql+asyncpg://tars:${POSTGRES_PASSWORD}@localhost:5432/tars
```

- [ ] **Step 3: Commit**

```bash
git add deploy/node1/docker-compose.yml
git commit -m "deploy: remove tars-backend from Docker, keep db + cloudflared"
```

---

### Task 4: Update config.py for direct deployment compatibility

**Files:**
- Modify: `backend/src/config.py`

- [ ] **Step 1: Add coding pipeline and Telegram settings**

Add these fields to the Settings class:

```python
# Coding pipeline
coding_max_plan_turns: int = 10
coding_max_worker_turns: int = 15
coding_max_review_turns: int = 5
coding_max_retries: int = 2
coding_repo_base_path: str = "/data/repos"

# Telegram
telegram_polling: bool = True  # True = polling, False = webhook (future)
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/config.py
git commit -m "config: add coding pipeline and telegram polling settings"
```

---

## Chunk 2: Telegram Incoming Message Handling

### Task 5: Create Telegram handler module

**Files:**
- Create: `backend/src/integrations/telegram_handlers.py`

This is the core new file. It creates a python-telegram-bot 21.x `Application` with handlers for:
- Text messages → route through orchestrator
- Slash commands → route through orchestrator
- Callback queries → route to approval manager or job handlers

- [ ] **Step 1: Write the handler module**

```python
"""Telegram incoming message and callback handlers.

Bridges python-telegram-bot 21.x Application to the T.A.R.S. orchestrator.
Handles:
- Text messages → orchestrator.process_message(source="telegram")
- Slash commands (/briefing, /jobs, /status) → same pipeline
- Callback queries (approve:uuid, reject:uuid, job_apply_id) → approval/job APIs
"""

from __future__ import annotations

import re
from uuid import UUID

import structlog
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from integrations.telegram_job_handlers import (
    handle_job_apply,
    handle_job_details,
    handle_job_skip,
    send_job_details_message,
)

log = structlog.get_logger()

# Module-level reference set during init
_application: Application | None = None
_chat_id: str = ""


async def _get_orchestrator():
    """Lazy import to avoid circular dependency."""
    from orchestrator.engine import get_orchestrator
    return get_orchestrator()


async def _get_approval_manager():
    """Lazy import to avoid circular dependency."""
    orchestrator = await _get_orchestrator()
    return orchestrator.approval_manager


# ------------------------------------------------------------------
# Text message handler
# ------------------------------------------------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle any text message from the user — route through orchestrator."""
    if not update.effective_message or not update.effective_message.text:
        return

    # Only respond to the configured chat
    if str(update.effective_chat.id) != _chat_id:
        log.warning("telegram_unauthorized_chat", chat_id=update.effective_chat.id)
        return

    text = update.effective_message.text.strip()
    if not text:
        return

    log.info("telegram_message_received", text=text[:100])

    try:
        orchestrator = await _get_orchestrator()
        response = await orchestrator.process_message(
            text=text,
            source="telegram",
        )

        reply_text = response.get("response", {}).get("text", "I couldn't process that.")
        # Truncate to Telegram's 4096 char limit
        if len(reply_text) > 4000:
            reply_text = reply_text[:4000] + "\n\n... (truncated)"

        await update.effective_message.reply_text(reply_text)

    except Exception:
        log.exception("telegram_message_handler_error")
        await update.effective_message.reply_text(
            "Something went wrong processing your message. Check the logs."
        )


# ------------------------------------------------------------------
# Callback query handler (inline keyboard buttons)
# ------------------------------------------------------------------

# Patterns: approve:{uuid}, reject:{uuid}, edit_approve:{uuid}
_APPROVAL_RE = re.compile(r"^(approve|reject|edit_approve):(.+)$")
# Patterns: job_apply_{id}, job_skip_{id}, job_details_{id}
_JOB_RE = re.compile(r"^job_(apply|skip|details)_(.+)$")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard button presses."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()  # Acknowledge the callback immediately

    data = query.data
    log.info("telegram_callback_received", callback_data=data)

    # --- Approval callbacks ---
    match = _APPROVAL_RE.match(data)
    if match:
        action, approval_id_str = match.groups()
        await _handle_approval_callback(query, action, approval_id_str)
        return

    # --- Job callbacks ---
    match = _JOB_RE.match(data)
    if match:
        action, job_id = match.groups()
        await _handle_job_callback(query, action, job_id)
        return

    # --- Briefing detail callback ---
    if data.startswith("briefing_detail:"):
        await query.edit_message_text("Full briefing details are available in the iOS app.")
        return

    log.warning("telegram_unknown_callback", data=data)


async def _handle_approval_callback(query, action: str, approval_id_str: str) -> None:
    """Process approval/reject/edit callbacks."""
    from db.session import get_db_session

    try:
        approval_id = UUID(approval_id_str)
    except ValueError:
        await query.edit_message_text("Invalid approval ID.")
        return

    approval_mgr = await _get_approval_manager()

    try:
        async with get_db_session() as session:
            if action == "approve":
                await approval_mgr.approve(session, approval_id, source="telegram")
                await query.edit_message_text(f"Approved. (via Telegram)")
                log.info("telegram_approval_decided", decision="approved", approval_id=str(approval_id))

            elif action == "reject":
                await approval_mgr.reject(session, approval_id, source="telegram")
                await query.edit_message_text(f"Rejected. (via Telegram)")
                log.info("telegram_approval_decided", decision="rejected", approval_id=str(approval_id))

            elif action == "edit_approve":
                # Edit not supported via Telegram — prompt to use iOS
                await query.edit_message_text(
                    "Edit & Approve requires the iOS app (richer editing UI). "
                    "Use Approve or Reject here, or open the app."
                )

    except Exception as exc:
        error_msg = str(exc)
        if "already" in error_msg.lower():
            await query.edit_message_text("This approval has already been decided.")
        elif "expired" in error_msg.lower():
            await query.edit_message_text("This approval has expired.")
        else:
            log.exception("telegram_approval_error", approval_id=approval_id_str)
            await query.edit_message_text(f"Error: {error_msg}")


async def _handle_job_callback(query, action: str, job_id: str) -> None:
    """Process job action callbacks (apply, skip, details)."""
    from integrations.telegram_bot import TelegramGateway

    try:
        if action == "skip":
            result = await handle_job_skip(job_id)
            await query.edit_message_text(f"Skipped: {result.get('title', job_id)}")

        elif action == "details":
            details = await handle_job_details(job_id)
            if details:
                # Send details as a new message (too long for edit)
                gateway = TelegramGateway.__instances__.get("default")
                if gateway:
                    await send_job_details_message(gateway, details)
                else:
                    await query.message.reply_text(str(details))

        elif action == "apply":
            result = await handle_job_apply(job_id)
            apply_method = result.get("apply_method", "unknown")
            await query.edit_message_text(
                f"Apply via {apply_method}. This requires approval (HC-01)."
            )

    except Exception:
        log.exception("telegram_job_callback_error", action=action, job_id=job_id)
        await query.edit_message_text(f"Error processing job action.")


# ------------------------------------------------------------------
# Application factory
# ------------------------------------------------------------------

def create_telegram_application(bot_token: str, chat_id: str) -> Application:
    """Create and configure the python-telegram-bot Application.

    The Application handles incoming updates via polling. It is started
    and stopped as part of the FastAPI lifespan.
    """
    global _chat_id
    _chat_id = chat_id

    app = Application.builder().token(bot_token).build()

    # Command handlers (order matters — more specific first)
    # All commands route through the same message handler since
    # the orchestrator's intent classifier handles /command parsing
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.COMMAND, handle_message))

    # Callback query handler for inline keyboard buttons
    app.add_handler(CallbackQueryHandler(handle_callback))

    log.info("telegram_application_created")
    return app
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/integrations/telegram_handlers.py
git commit -m "feat(telegram): add incoming message and callback handlers"
```

---

### Task 6: Wire Telegram Application into FastAPI lifespan

**Files:**
- Modify: `backend/src/main.py`

- [ ] **Step 1: Add Telegram Application startup/shutdown**

Add the Telegram Application initialization after the notification service init and start polling in the background. Add shutdown in the cleanup section.

In the lifespan, after `init_notification_service(...)`, add:

```python
    # Start Telegram bot polling (incoming messages + callbacks)
    from src.integrations.telegram_handlers import create_telegram_application

    tg_app = None
    if settings.telegram_bot_token and settings.telegram_chat_id:
        tg_app = create_telegram_application(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        log.info("telegram_polling_started")
```

In the shutdown section (before `scheduler.shutdown()`), add:

```python
    # Stop Telegram polling
    if tg_app is not None:
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        log.info("telegram_polling_stopped")
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/main.py
git commit -m "feat(telegram): wire Application polling into FastAPI lifespan"
```

---

### Task 7: Write tests for Telegram handlers

**Files:**
- Create: `backend/tests/test_telegram_handlers.py`

- [ ] **Step 1: Write handler tests**

```python
"""Tests for Telegram incoming message and callback handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


@pytest.fixture
def mock_update():
    """Create a mock Telegram Update object."""
    update = MagicMock()
    update.effective_chat.id = "12345"
    update.effective_message.text = "hello"
    update.effective_message.reply_text = AsyncMock()
    return update


@pytest.fixture
def mock_callback_update():
    """Create a mock callback query Update."""
    update = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()
    return update


class TestHandleMessage:
    """Tests for the text message handler."""

    @pytest.mark.asyncio
    async def test_routes_message_through_orchestrator(self, mock_update):
        from integrations.telegram_handlers import handle_message, _chat_id
        import integrations.telegram_handlers as mod

        mod._chat_id = "12345"

        mock_orchestrator = MagicMock()
        mock_orchestrator.process_message = AsyncMock(return_value={
            "response": {"text": "Hello from TARS!", "content_type": "text"},
        })

        with patch("integrations.telegram_handlers._get_orchestrator", return_value=mock_orchestrator):
            await handle_message(mock_update, MagicMock())

        mock_orchestrator.process_message.assert_called_once_with(
            text="hello",
            source="telegram",
        )
        mock_update.effective_message.reply_text.assert_called_once_with("Hello from TARS!")

    @pytest.mark.asyncio
    async def test_ignores_unauthorized_chat(self, mock_update):
        import integrations.telegram_handlers as mod
        mod._chat_id = "99999"  # different from update's chat id

        await mod.handle_message(mock_update, MagicMock())

        mock_update.effective_message.reply_text.assert_not_called()


class TestHandleCallback:
    """Tests for the callback query handler."""

    @pytest.mark.asyncio
    async def test_approve_callback(self, mock_callback_update):
        from integrations.telegram_handlers import handle_callback
        approval_id = uuid4()
        mock_callback_update.callback_query.data = f"approve:{approval_id}"

        mock_mgr = MagicMock()
        mock_mgr.approve = AsyncMock()

        with patch("integrations.telegram_handlers._get_approval_manager", return_value=mock_mgr), \
             patch("integrations.telegram_handlers.get_db_session") as mock_db:
            mock_db.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)
            await handle_callback(mock_callback_update, MagicMock())

        mock_callback_update.callback_query.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_reject_callback(self, mock_callback_update):
        from integrations.telegram_handlers import handle_callback
        approval_id = uuid4()
        mock_callback_update.callback_query.data = f"reject:{approval_id}"

        mock_mgr = MagicMock()
        mock_mgr.reject = AsyncMock()

        with patch("integrations.telegram_handlers._get_approval_manager", return_value=mock_mgr), \
             patch("integrations.telegram_handlers.get_db_session") as mock_db:
            mock_db.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)
            await handle_callback(mock_callback_update, MagicMock())

        mock_callback_update.callback_query.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_job_skip_callback(self, mock_callback_update):
        from integrations.telegram_handlers import handle_callback
        mock_callback_update.callback_query.data = "job_skip_abc123"

        with patch("integrations.telegram_handlers.handle_job_skip", new_callable=AsyncMock) as mock_skip:
            mock_skip.return_value = {"title": "Test Job"}
            await handle_callback(mock_callback_update, MagicMock())

        mock_skip.assert_called_once_with("abc123")

    @pytest.mark.asyncio
    async def test_unknown_callback_ignored(self, mock_callback_update):
        from integrations.telegram_handlers import handle_callback
        mock_callback_update.callback_query.data = "unknown_action"

        await handle_callback(mock_callback_update, MagicMock())
        mock_callback_update.callback_query.answer.assert_called_once()
```

- [ ] **Step 2: Run tests**

```bash
cd backend && .venv/bin/python -m pytest tests/test_telegram_handlers.py -v
```

Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_telegram_handlers.py
git commit -m "test(telegram): add handler routing tests"
```

---

## Chunk 3: Multi-Agent Coding Pipeline

### Task 8: Create the coding pipeline module

**Files:**
- Create: `backend/src/agents/coding_pipeline.py`

This module implements the planner → workers → reviewer → tester pattern.

- [ ] **Step 1: Write the pipeline**

```python
"""Multi-agent coding pipeline.

Orchestrates multiple Claude Code CLI instances:
    1. Planner — reads repo, creates implementation plan
    2. Workers — execute tasks in parallel (scoped context)
    3. Reviewer — reviews diff against plan
    4. Tester — runs tests (deterministic, no AI)

Each phase uses a fresh Claude instance with focused context.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog

from config import get_settings
from models.claude_spawner import ClaudeCodeSpawner

log = structlog.get_logger()


@dataclass
class PipelineTask:
    """A single task from the planner's output."""
    id: int
    description: str
    files: list[str]
    depends_on: list[int] = field(default_factory=list)


@dataclass
class PipelineResult:
    """Result of the full coding pipeline."""
    success: bool
    plan_summary: str = ""
    tasks_completed: int = 0
    tasks_total: int = 0
    review_passed: bool = False
    tests_passed: bool = False
    branch_name: str = ""
    diff_stats: str = ""
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class CodingPipeline:
    """Multi-agent coding pipeline using Claude Code CLI."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._spawner = ClaudeCodeSpawner()
        self._max_retries = self._settings.coding_max_retries
        self._repo_base = self._settings.coding_repo_base_path

    async def run(
        self,
        repo_url: str,
        task_description: str,
        branch: str = "main",
        notify_callback: Any = None,
    ) -> PipelineResult:
        """Run the full pipeline: plan → execute → review → test.

        Args:
            repo_url: GitHub repo URL or owner/repo shorthand.
            task_description: What to build/fix.
            branch: Base branch to work from.
            notify_callback: async callable(str) for progress notifications.
        """
        repo_path = self._resolve_repo_path(repo_url)
        work_branch = f"tars/{uuid4().hex[:8]}"

        async def notify(msg: str) -> None:
            if notify_callback:
                await notify_callback(msg)
            log.info("coding_pipeline_progress", message=msg)

        try:
            # Phase 0: Clone/update repo
            await notify("Cloning repository...")
            await self._prepare_repo(repo_url, repo_path, branch, work_branch)

            # Phase 1: Plan
            await notify("Planning implementation...")
            plan = await self._plan(repo_path, task_description)
            if not plan.tasks:
                return PipelineResult(
                    success=False,
                    error="Planner returned no tasks.",
                    plan_summary=plan.summary,
                )
            await notify(f"Plan ready: {len(plan.tasks)} tasks identified.")

            # Phase 2: Execute tasks
            completed = 0
            for batch in self._topological_batches(plan.tasks):
                results = await asyncio.gather(*[
                    self._execute_task(repo_path, task, plan.summary)
                    for task in batch
                ], return_exceptions=True)

                for task, result in zip(batch, results):
                    if isinstance(result, Exception):
                        log.error("coding_task_failed", task_id=task.id, error=str(result))
                    else:
                        completed += 1
                        await notify(f"Task {task.id}/{len(plan.tasks)} complete: {task.description[:60]}")

            # Phase 3: Review
            await notify("Reviewing changes...")
            review = await self._review(repo_path, plan.summary, branch)

            if not review["approved"]:
                # One retry: fix issues, then review again
                await notify(f"Review found issues. Fixing: {review['issues']}")
                await self._fix_issues(repo_path, review["issues"], plan.summary)
                review = await self._review(repo_path, plan.summary, branch)

            # Phase 4: Test
            await notify("Running tests...")
            test_result = await self._run_tests(repo_path)

            if not test_result["passed"]:
                # One retry: fix test failures
                await notify("Tests failed. Fixing...")
                await self._fix_tests(repo_path, test_result["output"], plan.summary)
                test_result = await self._run_tests(repo_path)

            # Get diff stats
            diff_stats = await self._get_diff_stats(repo_path, branch)

            if not test_result["passed"]:
                await notify("Tests still failing after retry. Stopping — needs human help.")
                return PipelineResult(
                    success=False,
                    plan_summary=plan.summary,
                    tasks_completed=completed,
                    tasks_total=len(plan.tasks),
                    review_passed=review.get("approved", False),
                    tests_passed=False,
                    branch_name=work_branch,
                    diff_stats=diff_stats,
                    error="Tests failing after retry.",
                )

            await notify("All tests passing. Ready to create PR.")

            return PipelineResult(
                success=True,
                plan_summary=plan.summary,
                tasks_completed=completed,
                tasks_total=len(plan.tasks),
                review_passed=review.get("approved", True),
                tests_passed=True,
                branch_name=work_branch,
                diff_stats=diff_stats,
            )

        except Exception as exc:
            log.exception("coding_pipeline_error")
            return PipelineResult(success=False, error=str(exc))

    # ------------------------------------------------------------------
    # Phase 1: Planning
    # ------------------------------------------------------------------

    @dataclass
    class _Plan:
        summary: str
        tasks: list[PipelineTask]
        test_commands: list[str]

    async def _plan(self, repo_path: str, task_description: str) -> _Plan:
        """Phase 1: Planner Claude reads repo structure and creates plan."""
        result = await self._spawner.execute(
            prompt=(
                f"You are a planner. Read the repository structure and create an "
                f"implementation plan.\n\n"
                f"Task: {task_description}\n\n"
                f"Output ONLY valid JSON:\n"
                f'{{"summary": "one line summary", '
                f'"tasks": [{{"id": 1, "description": "...", "files": ["path/to/file.py"], "depends_on": []}}], '
                f'"test_commands": ["pytest tests/ -v"]}}'
            ),
            mcp_profile="coding",
            working_directory=repo_path,
            max_turns=self._settings.coding_max_plan_turns,
            timeout=120,
        )

        parsed = self._parse_json(result.text)
        tasks = [
            PipelineTask(
                id=t["id"],
                description=t["description"],
                files=t.get("files", []),
                depends_on=t.get("depends_on", []),
            )
            for t in parsed.get("tasks", [])
        ]

        return self._Plan(
            summary=parsed.get("summary", task_description),
            tasks=tasks,
            test_commands=parsed.get("test_commands", ["pytest tests/ -v"]),
        )

    # ------------------------------------------------------------------
    # Phase 2: Worker execution
    # ------------------------------------------------------------------

    async def _execute_task(
        self, repo_path: str, task: PipelineTask, plan_summary: str,
    ) -> str:
        """Execute a single task with a fresh Claude instance."""
        result = await self._spawner.execute(
            prompt=(
                f"You are implementing task {task.id} of a plan.\n\n"
                f"Overall plan: {plan_summary}\n\n"
                f"Your task: {task.description}\n"
                f"Files to modify: {', '.join(task.files)}\n\n"
                f"ONLY modify the listed files. Commit your changes when done."
            ),
            mcp_profile="coding",
            working_directory=repo_path,
            max_turns=self._settings.coding_max_worker_turns,
            timeout=180,
        )

        if not result.success:
            raise RuntimeError(f"Task {task.id} failed: {result.error}")

        return result.text

    # ------------------------------------------------------------------
    # Phase 3: Review
    # ------------------------------------------------------------------

    async def _review(
        self, repo_path: str, plan_summary: str, base_branch: str,
    ) -> dict[str, Any]:
        """Review the diff against the plan with a fresh Claude instance."""
        diff = await asyncio.to_thread(
            subprocess.check_output,
            ["git", "diff", f"{base_branch}...HEAD", "--stat"],
            cwd=repo_path,
            text=True,
        )

        result = await self._spawner.execute(
            prompt=(
                f"You are a code reviewer. Review the changes against the plan.\n\n"
                f"Plan: {plan_summary}\n\n"
                f"Diff summary:\n{diff[:3000]}\n\n"
                f"Check for: missing items, security issues, convention violations, bugs.\n\n"
                f'Output ONLY JSON: {{"approved": true/false, "issues": ["issue1", "issue2"]}}'
            ),
            mcp_profile="coding",
            working_directory=repo_path,
            max_turns=self._settings.coding_max_review_turns,
            timeout=90,
        )

        return self._parse_json(result.text)

    async def _fix_issues(
        self, repo_path: str, issues: list[str], plan_summary: str,
    ) -> None:
        """Fix review issues."""
        await self._spawner.execute(
            prompt=(
                f"Fix these review issues:\n"
                + "\n".join(f"- {i}" for i in issues)
                + f"\n\nOriginal plan: {plan_summary}\n"
                f"Commit your fixes."
            ),
            mcp_profile="coding",
            working_directory=repo_path,
            max_turns=self._settings.coding_max_worker_turns,
            timeout=180,
        )

    # ------------------------------------------------------------------
    # Phase 4: Testing (deterministic, no AI)
    # ------------------------------------------------------------------

    async def _run_tests(self, repo_path: str) -> dict[str, Any]:
        """Run tests. Returns {passed: bool, output: str}."""
        try:
            output = await asyncio.to_thread(
                subprocess.check_output,
                ["python", "-m", "pytest", "tests/", "-v", "--tb=short"],
                cwd=repo_path,
                text=True,
                stderr=subprocess.STDOUT,
                timeout=120,
            )
            return {"passed": True, "output": output}
        except subprocess.CalledProcessError as exc:
            return {"passed": False, "output": exc.output or str(exc)}
        except subprocess.TimeoutExpired:
            return {"passed": False, "output": "Tests timed out after 120s"}

    async def _fix_tests(
        self, repo_path: str, test_output: str, plan_summary: str,
    ) -> None:
        """Fix failing tests."""
        await self._spawner.execute(
            prompt=(
                f"Tests are failing. Fix them.\n\n"
                f"Test output:\n{test_output[:3000]}\n\n"
                f"Original plan: {plan_summary}\n"
                f"Commit your fixes."
            ),
            mcp_profile="coding",
            working_directory=repo_path,
            max_turns=self._settings.coding_max_worker_turns,
            timeout=180,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_repo_path(self, repo_url: str) -> str:
        """Convert repo URL to local path."""
        # Handle owner/repo shorthand
        if "/" in repo_url and not repo_url.startswith("http"):
            repo_name = repo_url.split("/")[-1]
        else:
            repo_name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
        return os.path.join(self._repo_base, repo_name)

    async def _prepare_repo(
        self, repo_url: str, repo_path: str, branch: str, work_branch: str,
    ) -> None:
        """Clone or update the repo and create a working branch."""
        if not os.path.isdir(repo_path):
            full_url = repo_url if repo_url.startswith("http") else f"https://github.com/{repo_url}.git"
            await asyncio.to_thread(
                subprocess.check_call,
                ["git", "clone", full_url, repo_path],
                timeout=120,
            )
        else:
            await asyncio.to_thread(
                subprocess.check_call,
                ["git", "fetch", "origin"],
                cwd=repo_path,
                timeout=60,
            )
            await asyncio.to_thread(
                subprocess.check_call,
                ["git", "checkout", branch],
                cwd=repo_path,
            )
            await asyncio.to_thread(
                subprocess.check_call,
                ["git", "pull", "origin", branch],
                cwd=repo_path,
                timeout=60,
            )

        # Create working branch
        await asyncio.to_thread(
            subprocess.check_call,
            ["git", "checkout", "-b", work_branch],
            cwd=repo_path,
        )

    async def _get_diff_stats(self, repo_path: str, base_branch: str) -> str:
        """Get diff statistics."""
        try:
            return await asyncio.to_thread(
                subprocess.check_output,
                ["git", "diff", f"{base_branch}...HEAD", "--shortstat"],
                cwd=repo_path,
                text=True,
            )
        except Exception:
            return ""

    def _topological_batches(self, tasks: list[PipelineTask]) -> list[list[PipelineTask]]:
        """Group tasks into batches respecting dependencies.

        Independent tasks run in parallel; dependent tasks wait for their
        prerequisites to complete first.
        """
        completed: set[int] = set()
        remaining = list(tasks)
        batches: list[list[PipelineTask]] = []

        while remaining:
            batch = [t for t in remaining if all(d in completed for d in t.depends_on)]
            if not batch:
                # Circular dependency or missing task — just run remaining sequentially
                batches.append(remaining)
                break
            batches.append(batch)
            completed.update(t.id for t in batch)
            remaining = [t for t in remaining if t.id not in completed]

        return batches

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """Parse JSON from Claude output, handling markdown fences."""
        # Strip markdown code fences
        clean = text.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            lines = lines[1:]  # Remove opening fence
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            clean = "\n".join(lines)

        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            # Try to find JSON object in the text
            start = clean.find("{")
            end = clean.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(clean[start:end])
                except json.JSONDecodeError:
                    pass
            return {"approved": False, "issues": ["Could not parse response"]}
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/agents/coding_pipeline.py
git commit -m "feat(coding): add multi-agent planner/worker/reviewer pipeline"
```

---

### Task 9: Update coding agent to use local pipeline

**Files:**
- Modify: `backend/src/agents/coding.py`

- [ ] **Step 1: Update coding agent to use CodingPipeline for complex tasks**

The coding agent should detect task complexity:
- **Simple tasks** (quick questions, small fixes): Direct Claude spawn, immediate response
- **Complex tasks** (refactors, new features, multi-file changes): Background CodingPipeline

Add to the `execute()` method, early in the function, after parsing:

```python
# Detect if this needs the multi-agent pipeline
if _is_complex_task(task_description):
    return await self._run_pipeline(repo_url, task_description, branch, context)
```

Add the `_run_pipeline` method:

```python
async def _run_pipeline(
    self, repo_url: str, task: str, branch: str, context: AgentContext,
) -> AgentResult:
    """Run the multi-agent coding pipeline for complex tasks."""
    from agents.coding_pipeline import CodingPipeline
    from integrations.notification_service import get_notification_service

    pipeline = CodingPipeline()

    # Notify callback sends progress to user via all channels
    notifier = get_notification_service()

    async def notify(msg: str) -> None:
        if notifier:
            await notifier.send(msg, severity="info")

    # Run pipeline in background task
    result = await pipeline.run(
        repo_url=repo_url,
        task_description=task,
        branch=branch,
        notify_callback=notify,
    )

    if result.success:
        return AgentResult(
            success=True,
            text=(
                f"Implementation complete on branch `{result.branch_name}`.\n\n"
                f"**Plan:** {result.plan_summary}\n"
                f"**Tasks:** {result.tasks_completed}/{result.tasks_total} completed\n"
                f"**Review:** {'Passed' if result.review_passed else 'Issues found'}\n"
                f"**Tests:** {'All passing' if result.tests_passed else 'Failing'}\n"
                f"**Changes:** {result.diff_stats}"
            ),
            has_side_effects=True,
            action_type="create_pr",
            approval_title=f"Create PR: {result.plan_summary}",
            preview={
                "branch": result.branch_name,
                "summary": result.plan_summary,
                "diff_stats": result.diff_stats,
                "tasks_completed": result.tasks_completed,
            },
        )
    else:
        return AgentResult(
            success=False,
            text=f"Pipeline failed: {result.error}",
            error="pipeline_failed",
        )
```

Add the complexity detector:

```python
def _is_complex_task(description: str) -> bool:
    """Detect if a coding task needs the multi-agent pipeline."""
    complex_keywords = [
        "refactor", "implement", "add feature", "build",
        "create", "migrate", "redesign", "rewrite",
        "add oauth", "add auth", "add api", "new endpoint",
    ]
    desc_lower = description.lower()
    return any(kw in desc_lower for kw in complex_keywords)
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/agents/coding.py
git commit -m "feat(coding): route complex tasks to multi-agent pipeline"
```

---

### Task 10: Write tests for coding pipeline

**Files:**
- Create: `backend/tests/test_coding_pipeline.py`

- [ ] **Step 1: Write pipeline tests**

```python
"""Tests for multi-agent coding pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.coding_pipeline import CodingPipeline, PipelineTask


class TestTopologicalBatches:
    """Test dependency-based task batching."""

    def test_independent_tasks_single_batch(self):
        pipeline = CodingPipeline.__new__(CodingPipeline)
        tasks = [
            PipelineTask(id=1, description="A", files=["a.py"]),
            PipelineTask(id=2, description="B", files=["b.py"]),
            PipelineTask(id=3, description="C", files=["c.py"]),
        ]
        batches = pipeline._topological_batches(tasks)
        assert len(batches) == 1
        assert len(batches[0]) == 3

    def test_sequential_dependencies(self):
        pipeline = CodingPipeline.__new__(CodingPipeline)
        tasks = [
            PipelineTask(id=1, description="A", files=["a.py"]),
            PipelineTask(id=2, description="B", files=["b.py"], depends_on=[1]),
            PipelineTask(id=3, description="C", files=["c.py"], depends_on=[2]),
        ]
        batches = pipeline._topological_batches(tasks)
        assert len(batches) == 3
        assert batches[0][0].id == 1
        assert batches[1][0].id == 2
        assert batches[2][0].id == 3

    def test_mixed_dependencies(self):
        pipeline = CodingPipeline.__new__(CodingPipeline)
        tasks = [
            PipelineTask(id=1, description="A", files=["a.py"]),
            PipelineTask(id=2, description="B", files=["b.py"]),
            PipelineTask(id=3, description="C", files=["c.py"], depends_on=[1, 2]),
        ]
        batches = pipeline._topological_batches(tasks)
        assert len(batches) == 2
        assert {t.id for t in batches[0]} == {1, 2}
        assert batches[1][0].id == 3


class TestParseJson:
    """Test JSON parsing from Claude output."""

    def test_plain_json(self):
        result = CodingPipeline._parse_json('{"approved": true, "issues": []}')
        assert result["approved"] is True

    def test_json_in_markdown_fence(self):
        text = '```json\n{"approved": false, "issues": ["missing test"]}\n```'
        result = CodingPipeline._parse_json(text)
        assert result["approved"] is False
        assert "missing test" in result["issues"]

    def test_json_embedded_in_text(self):
        text = 'Here is my review:\n{"approved": true, "issues": []}\nDone.'
        result = CodingPipeline._parse_json(text)
        assert result["approved"] is True

    def test_invalid_json_returns_fallback(self):
        result = CodingPipeline._parse_json("not json at all")
        assert result["approved"] is False
```

- [ ] **Step 2: Run tests**

```bash
cd backend && .venv/bin/python -m pytest tests/test_coding_pipeline.py -v
```

Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_coding_pipeline.py
git commit -m "test(coding): add pipeline topology and JSON parsing tests"
```

---

## Chunk 4: Deployment & End-to-End Testing

### Task 11: Deploy to Node 1

- [ ] **Step 1: Push all changes to GitHub**

```bash
git push origin main
```

- [ ] **Step 2: SSH into Node 1 and run setup**

```bash
ssh tasin@tars-brain

# Pull latest code
cd /opt/tars && git pull origin main

# Run setup script (installs Python, Node.js, Claude CLI, creates venv)
bash deploy/scripts/setup-node1-direct.sh

# Authenticate Claude Code CLI
claude login

# Stop the Docker backend (it's being replaced)
cd /opt/tars/deploy/node1
docker compose down tars-backend

# Update docker-compose and restart DB + Cloudflared
docker compose up -d

# Update .env: DATABASE_URL to localhost
# Edit /opt/tars/deploy/node1/.env:
#   DATABASE_URL=postgresql+asyncpg://tars:PASSWORD@localhost:5432/tars

# Run Alembic migrations from host
cd /opt/tars/backend
.venv/bin/python -m alembic upgrade head

# Start the backend via systemd
sudo systemctl start tars-backend
sudo journalctl -u tars-backend -f
```

- [ ] **Step 3: Verify backend starts**

Check logs for:
```
tars_online, node_role=brain, scheduler=started
agent_registered (x14 agents)
telegram_polling_started
```

- [ ] **Step 4: Verify health endpoint**

```bash
curl http://localhost:8000/api/v1/health
```

---

### Task 12: End-to-end Telegram test

- [ ] **Step 1: Send a text message to the bot on Telegram**

Message: "hello"

Expected: T.A.R.S. responds with a greeting (routed through orchestrator → general intent → Gemini Flash).

- [ ] **Step 2: Test a slash command**

Message: "/briefing"

Expected: Briefing agent runs — fetches weather, calendar, emails. Responds with a composed narrative.

- [ ] **Step 3: Test approval flow**

Message: "draft an email to John about the meeting tomorrow"

Expected:
1. Communication agent drafts email via Claude
2. Approval card appears with Approve/Reject buttons
3. Tap "Approve" → message updates to "Approved. (via Telegram)"

- [ ] **Step 4: Test job digest buttons**

If job listings exist, test Skip/Details buttons on a job card.

---

### Task 13: End-to-end iOS test

- [ ] **Step 1: Open Xcode project**

```
ios/TARS/TARS.xcodeproj
```

- [ ] **Step 2: Configure signing (free account)**

For each target (TARS, TARSWatch, TARSWidgetExtension):
- Set Team to your free Apple ID
- Add `.dev` suffix to bundle ID
- Remove HealthKit, Siri, APNs entitlements from `TARS.entitlements`

- [ ] **Step 3: Build and run on device (Cmd+R)**

- [ ] **Step 4: Configure backend in Settings tab**

- Server URL: `http://100.94.4.103:8000` (Tailscale IP)
- API Key: your `TARS_API_KEY` value

- [ ] **Step 5: Test chat**

Send "hello" → verify response appears.

- [ ] **Step 6: Test briefing**

Tap Briefing tab → verify sections load (weather, schedule, etc.)

- [ ] **Step 7: Test approval**

Send "draft email to someone about a meeting" → verify approval card appears with Approve/Reject buttons.

- [ ] **Step 8: Test schedule**

Tap Schedule tab → verify today's calendar events appear.

---

### Task 14: Verify Claude Code integration

- [ ] **Step 1: Test Claude is available**

```bash
# On Node 1
claude --version
claude --print -p "Say hello"
```

- [ ] **Step 2: Test via API**

```bash
curl -X POST http://localhost:8000/api/v1/message \
  -H "Authorization: Bearer $TARS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "research the latest FastAPI security best practices", "source": "system"}'
```

Expected: ResearchAgent uses Claude with Brave Search MCP to produce structured research output.

- [ ] **Step 3: Test from Telegram**

Send to bot: "research FastAPI security best practices"

Expected: Same research output delivered via Telegram.

---

## Deployment Checklist Summary

| Step | Action | Verify |
|------|--------|--------|
| 1 | Push code to GitHub | CI/CD passes |
| 2 | SSH to Node 1, pull code | `git pull` succeeds |
| 3 | Run setup script | Python, Node.js, Claude CLI installed |
| 4 | `claude login` | Authentication succeeds |
| 5 | Stop Docker backend | `docker compose down tars-backend` |
| 6 | Update docker-compose | DB + cloudflared only |
| 7 | Update .env DATABASE_URL | `localhost:5432` |
| 8 | Run migrations | `alembic upgrade head` |
| 9 | Start systemd service | `systemctl start tars-backend` |
| 10 | Health check | `curl localhost:8000/api/v1/health` |
| 11 | Test Telegram | Send message, get response |
| 12 | Test iOS | Configure, send message, get response |
| 13 | Test Claude | Research query returns real results |
| 14 | Clean up old Docker images | `docker image prune -a` |
