"""Aggregate all API sub-routers into a single router."""

from __future__ import annotations

from fastapi import APIRouter

from src.api.approvals import router as approvals_router
from src.api.briefings import router as briefings_router
from src.api.budgets import router as budgets_router
from src.api.config_api import router as config_router
from src.api.contacts_sync import router as contacts_router
from src.api.deploy import router as deploy_router
from src.api.device_tokens import router as device_tokens_router
from src.api.finance import router as finance_router
from src.api.health import router as health_router
from src.api.health_data import router as health_data_router
from src.api.jobs import router as jobs_router
from src.api.messages import router as messages_router
from src.api.wardrobe import router as wardrobe_router
from src.api.websocket import router as websocket_router
from src.api.workout import router as workout_router

router = APIRouter()

router.include_router(health_router)
router.include_router(messages_router)
router.include_router(approvals_router)
router.include_router(briefings_router)
router.include_router(wardrobe_router)
router.include_router(websocket_router)
router.include_router(config_router)
router.include_router(finance_router)
router.include_router(budgets_router)
router.include_router(deploy_router)
router.include_router(jobs_router)
router.include_router(health_data_router)
router.include_router(contacts_router)
router.include_router(workout_router)
router.include_router(device_tokens_router)
