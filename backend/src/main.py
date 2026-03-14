"""T.A.R.S. — FastAPI application entry point."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from src.api.config_api import seed_default_config
from src.api.router import router
from src.config import get_settings
from src.db.session import async_session_factory, close_db, init_db
from src.integrations.apns_client import APNsClient
from src.integrations.notification_service import init_notification_service
from src.integrations.telegram_bot import TelegramGateway
from src.orchestrator.engine import get_orchestrator
from src.scheduler.jobs import create_scheduler
from src.utils.logger import setup_logging
from src.wake_word.listener import (
    check_mic_available,
    check_models_exist,
    get_wake_word_listener,
    init_wake_word_listener,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Startup and shutdown lifecycle for the T.A.R.S. backend."""
    settings = get_settings()
    setup_logging(log_level=settings.log_level)
    log = structlog.get_logger()

    await init_db()

    # Seed default config on first run
    async with async_session_factory() as session:
        await seed_default_config(session)

    # Initialise APNs client (None if key/config missing)
    apns_client: APNsClient | None = None
    if settings.apns_key_id and settings.apns_team_id and settings.apns_bundle_id:
        apns_client = APNsClient(
            key_path=settings.apns_key_path,
            key_id=settings.apns_key_id,
            team_id=settings.apns_team_id,
            bundle_id=settings.apns_bundle_id,
            use_sandbox=settings.apns_use_sandbox,
        )

    # Initialise notification service (Telegram + WebSocket + APNs)
    telegram = TelegramGateway(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )
    init_notification_service(telegram_gateway=telegram, apns_client=apns_client)

    # Start Telegram polling (incoming messages + callbacks)
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

    # Start the orchestrator, register agents, and start scheduler
    orchestrator = get_orchestrator()
    _register_agents(orchestrator)
    scheduler = create_scheduler(orchestrator)
    scheduler.start()
    log.info("tars_online", node_role=settings.node_role, scheduler="started")

    # Start wake word listener (only if USB mic detected and models exist)
    wake_word_task: asyncio.Task | None = None
    if check_mic_available(settings.usb_mic_device_index):
        if check_models_exist(settings.wake_word_model_paths):
            ww_listener = init_wake_word_listener()
            wake_word_task = asyncio.create_task(ww_listener.start())
            log.info("wake_word_daemon_started")
        else:
            log.warning(
                "wake_word_skipped_no_models",
                expected=settings.wake_word_model_paths,
            )
    else:
        log.warning("wake_word_skipped_no_mic")

    yield

    # Shutdown wake word listener
    if wake_word_task is not None:
        ww_listener = get_wake_word_listener()
        if ww_listener:
            await ww_listener.stop()
        wake_word_task.cancel()
        try:
            await wake_word_task
        except asyncio.CancelledError:
            pass
        log.info("wake_word_daemon_stopped")

    # Shutdown Telegram polling
    if tg_app is not None:
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        log.info("telegram_polling_stopped")

    scheduler.shutdown(wait=False)
    log.info("scheduler_stopped")
    await close_db()
    log.info("tars_shutting_down")


app = FastAPI(
    title="T.A.R.S. API",
    description="Tasin's Autonomous Resource System",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)


def _register_agents(orchestrator: object) -> None:
    """Instantiate and register all agents with the orchestrator."""
    from src.agents.briefing import BriefingAgent
    from src.agents.coding import CodingAgent
    from src.agents.communication import CommunicationAgent
    from src.agents.daily_life import DailyLifeAgent
    from src.agents.email_classifier import EmailClassifierAgent
    from src.agents.eod_summary import EODSummaryAgent
    from src.agents.fashion import FashionAgent
    from src.agents.finance import FinanceAgent
    from src.agents.health_fitness import HealthFitnessAgent
    from src.agents.health_monitor import HealthMonitorAgent
    from src.agents.job_search import JobSearchAgent
    from src.agents.product_research import ProductResearchAgent
    from src.agents.research import ResearchAgent
    from src.agents.workout_tracker import WorkoutTrackerAgent

    agents = [
        BriefingAgent(),
        EmailClassifierAgent(),
        CommunicationAgent(),
        DailyLifeAgent(),
        JobSearchAgent(),
        FashionAgent(),
        ProductResearchAgent(),
        CodingAgent(),
        ResearchAgent(),
        HealthMonitorAgent(),
        FinanceAgent(),
        HealthFitnessAgent(),
        EODSummaryAgent(),
        WorkoutTrackerAgent(),
    ]

    for agent in agents:
        orchestrator.register_agent(agent)
