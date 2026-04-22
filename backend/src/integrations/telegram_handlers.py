"""Telegram incoming message and callback handlers for T.A.R.S.

Handles:
- Text messages routed through the orchestrator
- Inline keyboard callbacks (approvals, jobs, briefings)

This module complements ``telegram_bot.py`` (send-only gateway) by adding
receive capabilities via python-telegram-bot's Application polling.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import structlog
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

log = structlog.get_logger()

# Module-level chat ID set by create_telegram_application().
# Only messages from this chat are processed (security).
_chat_id: str | None = None

# Maximum reply length for Telegram messages.
_MAX_REPLY_LENGTH = 4000

# Callback data patterns
_APPROVAL_RE = re.compile(r"^(approve|reject|edit_approve):(.+)$")
_JOB_RE = re.compile(r"^job_(apply|skip|details)_(.+)$")


# ---------------------------------------------------------------------------
# Message handler
# ---------------------------------------------------------------------------


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages by routing through the orchestrator."""
    if not _is_authorized(update):
        log.warning(
            "telegram_unauthorized_message",
            chat_id=str(update.effective_chat.id) if update.effective_chat else None,
        )
        return

    text = update.message.text if update.message else None
    if not text:
        return

    log.info("telegram_message_received", text_preview=text[:80])

    try:
        # Lazy import to avoid circular dependencies
        from src.orchestrator.engine import get_orchestrator

        orchestrator = get_orchestrator()
        response = await orchestrator.process_message(text=text, source="telegram")

        reply_text = response.get("response", {}).get("text", "")
        if not reply_text:
            reply_text = "Processed, but no response text was generated."

        # Truncate if needed
        if len(reply_text) > _MAX_REPLY_LENGTH:
            reply_text = reply_text[:_MAX_REPLY_LENGTH] + "\n\n... (truncated)"

        await update.message.reply_text(reply_text)

    except Exception:
        log.exception("telegram_message_handler_error")
        try:
            await update.message.reply_text("Sorry, something went wrong processing your message.")
        except Exception:
            log.exception("telegram_error_reply_failed")


# ---------------------------------------------------------------------------
# Callback query handler
# ---------------------------------------------------------------------------


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard button presses."""
    query = update.callback_query
    if query is None:
        return

    # Always acknowledge the callback immediately
    await query.answer()

    data = query.data or ""
    log.info("telegram_callback_received", callback_data=data)

    # --- Approval callbacks ---
    approval_match = _APPROVAL_RE.match(data)
    if approval_match:
        action = approval_match.group(1)
        approval_id_str = approval_match.group(2)
        await _handle_approval_callback(query, action, approval_id_str)
        return

    # --- Job callbacks ---
    job_match = _JOB_RE.match(data)
    if job_match:
        action = job_match.group(1)
        job_id = job_match.group(2)
        await _handle_job_callback(query, action, job_id)
        return

    # --- Briefing detail callback ---
    if data.startswith("briefing_detail:"):
        await query.edit_message_text(
            text=(query.message.text or "") + "\n\nFull details available in iOS app.",
            parse_mode=None,
        )
        return

    log.warning("telegram_unknown_callback", callback_data=data)


# ---------------------------------------------------------------------------
# Approval sub-handler
# ---------------------------------------------------------------------------


async def _handle_approval_callback(
    query: Any,
    action: str,
    approval_id_str: str,
) -> None:
    """Process approve / reject / edit_approve callbacks."""
    if action == "edit_approve":
        await query.edit_message_text(
            text=(query.message.text or "") + "\n\nEdit requires iOS app.",
            parse_mode=None,
        )
        return

    try:
        # Lazy imports to avoid circular dependencies
        from src.db.session import get_db_session
        from src.orchestrator.engine import get_orchestrator

        orchestrator = get_orchestrator()
        approval_manager = orchestrator.approval_manager
        aid = UUID(approval_id_str)

        async with get_db_session() as session:
            if action == "approve":
                await approval_manager.approve(session, aid, source="telegram")
                status_text = "Approved"
            else:  # reject
                await approval_manager.reject(session, aid, source="telegram")
                status_text = "Rejected"

        await query.edit_message_text(
            text=(query.message.text or "") + f"\n\n{status_text} via Telegram.",
            parse_mode=None,
        )
        log.info("telegram_approval_decided", approval_id=approval_id_str, decision=action)

    except Exception as exc:
        error_msg = str(exc).lower()
        if "already" in error_msg:
            reply = "This approval has already been decided."
        elif "expired" in error_msg:
            reply = "This approval has expired."
        else:
            reply = f"Error processing approval: {exc}"
            log.exception("telegram_approval_error", approval_id=approval_id_str)

        await query.edit_message_text(
            text=(query.message.text or "") + f"\n\n{reply}",
            parse_mode=None,
        )


# ---------------------------------------------------------------------------
# Job sub-handler
# ---------------------------------------------------------------------------


async def _handle_job_callback(query: Any, action: str, job_id: str) -> None:
    """Process job apply / skip / details callbacks."""
    try:
        from src.integrations.notification_service import get_notification_service
        from src.integrations.telegram_job_handlers import (
            handle_job_apply,
            handle_job_details,
            handle_job_skip,
            send_job_details_message,
        )

        if action == "apply":
            result = await handle_job_apply(job_id)
            if result.get("success"):
                await query.edit_message_text(
                    text=(query.message.text or "")
                    + f"\n\nApply initiated for {result.get('title', 'job')} at {result.get('company', '')}.",
                    parse_mode=None,
                )
            else:
                await query.edit_message_text(
                    text=(query.message.text or "") + f"\n\n{result.get('message', 'Job not found.')}",
                    parse_mode=None,
                )

        elif action == "skip":
            result = await handle_job_skip(job_id)
            await query.edit_message_text(
                text=(query.message.text or "") + f"\n\n{result.get('message', 'Skipped.')}",
                parse_mode=None,
            )

        elif action == "details":
            details = await handle_job_details(job_id)
            if details.get("success"):
                # Get the notification service's telegram gateway to send details
                try:
                    ns = get_notification_service()
                    await send_job_details_message(ns._telegram, details)
                except Exception:
                    # Fallback: just send a summary in the callback reply
                    summary = (
                        f"{details.get('title', '')} at {details.get('company', '')}\n"
                        f"Match: {details.get('match_score', 0)}%\n"
                        f"URL: {details.get('url', 'N/A')}"
                    )
                    await query.edit_message_text(
                        text=(query.message.text or "") + f"\n\n{summary}",
                        parse_mode=None,
                    )
            else:
                await query.edit_message_text(
                    text=(query.message.text or "") + f"\n\n{details.get('message', 'Job not found.')}",
                    parse_mode=None,
                )

    except Exception:
        log.exception("telegram_job_callback_error", job_id=job_id, action=action)
        await query.edit_message_text(
            text=(query.message.text or "") + "\n\nError processing job action.",
            parse_mode=None,
        )


# ---------------------------------------------------------------------------
# Authorization helper
# ---------------------------------------------------------------------------


def _is_authorized(update: Update) -> bool:
    """Check if the message comes from the configured chat."""
    if _chat_id is None:
        return False
    if update.effective_chat is None:
        return False
    return str(update.effective_chat.id) == _chat_id


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_telegram_application(bot_token: str, chat_id: str) -> Application:
    """Create a python-telegram-bot Application with message and callback handlers.

    Parameters
    ----------
    bot_token:
        Telegram bot token.
    chat_id:
        Allowed chat ID. Only messages from this chat are processed.

    Returns
    -------
    Application
        Configured but not yet initialized Application instance.
    """
    global _chat_id
    _chat_id = chat_id

    application = Application.builder().token(bot_token).build()

    # Text messages (non-command) -> orchestrator
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Command messages -> also through orchestrator
    application.add_handler(MessageHandler(filters.COMMAND, handle_message))

    # Inline keyboard callbacks
    application.add_handler(CallbackQueryHandler(handle_callback))

    log.info("telegram_application_created", chat_id=chat_id)
    return application
