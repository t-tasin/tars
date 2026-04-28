"""Workout Tracker agent — manages splits, progressive overload, and accountability.

Tier 1 (autonomous) — internal data writes only, no external side effects.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from shared.constants import AutonomyClass

from agents.base import AgentContext, AgentResult, BaseAgent

log = structlog.get_logger()

_STREAK_MILESTONES = {7, 14, 30, 60, 90, 180, 365}


class WorkoutTrackerAgent(BaseAgent):
    """Manage workout splits, log sets, enforce progressive overload, track streaks."""

    agent_type = "workout_tracker"
    autonomy_class = AutonomyClass.WRITE_LOCAL

    async def execute(self, context: AgentContext) -> AgentResult:
        """Route to the appropriate sub-action based on context."""
        from db.repositories.workout import WorkoutRepository
        from db.session import get_db_session

        async with get_db_session() as session:
            repo = WorkoutRepository(session)

            # Check for active session to handle voice logging
            today_session = await repo.get_today_session()

            if today_session and today_session.status.value == "active":
                # Try to parse as a set log via Gemini
                parsed = await self._parse_voice_log(context, today_session)
                if parsed:
                    return parsed

            # Default: show today's workout status
            return await self._show_today_status(repo, today_session)

    async def _show_today_status(self, repo: Any, today_session: Any) -> AgentResult:
        """Show today's workout status."""
        if today_session is None:
            split = await repo.get_active_split()
            if split is None:
                return AgentResult(
                    autonomy_class=self.autonomy_class,
                    success=True,
                    text="No workout split configured. Set one up in the app or tell me your split.",
                )
            return AgentResult(
                autonomy_class=self.autonomy_class,
                success=True,
                text="No workout scheduled for today. Rest up!",
            )

        status = today_session.status.value
        day = today_session.day_name

        if status == "pending":
            unique_exercises: set[Any] = set()
            exercise_list: list[str] = []
            for log_entry in today_session.logs:
                if log_entry.exercise_id not in unique_exercises:
                    unique_exercises.add(log_entry.exercise_id)
                    exercise_list.append(f"• {log_entry.target_reps} reps × {log_entry.target_weight}lbs")
            return AgentResult(
                autonomy_class=self.autonomy_class,
                success=True,
                text=f"Today is {day} day. Tap Start when you're ready.\n" + "\n".join(exercise_list),
                content_type="card",
                cards=[
                    {
                        "type": "workout_session",
                        "session_id": str(today_session.id),
                        "day_name": day,
                        "status": status,
                    }
                ],
            )

        if status == "active":
            total_sets = len(today_session.logs)
            logged_sets = sum(1 for log in today_session.logs if log.actual_reps is not None)
            return AgentResult(
                autonomy_class=self.autonomy_class,
                success=True,
                text=f"{day.title()} day in progress. {logged_sets}/{total_sets} sets logged.",
            )

        if status == "completed":
            return AgentResult(
                autonomy_class=self.autonomy_class,
                success=True,
                text=f"{day.title()} day complete! Nice work.",
            )

        return AgentResult(autonomy_class=self.autonomy_class, success=True, text=f"Today's {day} day was skipped.")

    async def _parse_voice_log(self, context: AgentContext, session: Any) -> AgentResult | None:
        """Attempt to parse a voice message as a set log using Gemini Flash."""
        from models.gemini_client import GeminiClient

        gemini: GeminiClient | None = context.config.get("gemini_client")
        if gemini is None:
            return None

        # Build exercise context for the LLM
        exercises_context: dict[str, dict[str, Any]] = {}
        for log_entry in session.logs:
            eid = str(log_entry.exercise_id)
            if eid not in exercises_context:
                exercises_context[eid] = {
                    "exercise_id": eid,
                    "name": "Exercise",
                    "sets": [],
                }
            exercises_context[eid]["sets"].append(
                {
                    "set_number": log_entry.set_number,
                    "target_reps": log_entry.target_reps,
                    "target_weight": float(log_entry.target_weight),
                    "logged": log_entry.actual_reps is not None,
                }
            )

        prompt = (
            "Parse this gym voice log into structured data. "
            "The user is logging a workout set. Extract:\n"
            "- exercise_name (string)\n"
            "- set_number (int)\n"
            "- actual_reps (int)\n"
            "- actual_weight (float)\n\n"
            f'User said: "{context.user_message}"\n\n'
            f"Today's exercises: {json.dumps(list(exercises_context.values()), indent=2)}\n\n"
            'Respond with ONLY valid JSON. If you can\'t parse it, respond with {"error": "reason"}.'
        )

        try:
            response = await gemini.generate(
                prompt=prompt,
                model="gemini-2.5-flash",
                temperature=0.1,
                max_output_tokens=256,
            )

            parsed = json.loads(response.text.strip().strip("```json").strip("```"))

            if "error" in parsed:
                return None  # Not a voice log, fall through to default handler

            log.info("workout_voice_log_parsed", parsed=parsed)

            # Log the set via repository
            from db.repositories.workout import WorkoutRepository
            from db.session import get_db_session

            async with get_db_session() as db_session:
                repo = WorkoutRepository(db_session)
                entry = await repo.log_set(
                    session_id=session.id,
                    exercise_id=parsed.get("exercise_id", session.logs[0].exercise_id),
                    set_number=parsed["set_number"],
                    actual_reps=parsed["actual_reps"],
                    actual_weight=parsed["actual_weight"],
                )

            if entry:
                return AgentResult(
                    autonomy_class=self.autonomy_class,
                    success=True,
                    text=f"Logged: Set {parsed['set_number']} — {parsed['actual_reps']} reps at {parsed['actual_weight']}lbs.",
                )

        except Exception:
            log.warning("workout_voice_parse_failed", exc_info=True)

        return None

    @staticmethod
    def _should_progress(logs: list[Any]) -> bool:
        """Check if all sets hit target reps and weight."""
        return all(
            entry.actual_reps is not None
            and entry.actual_weight is not None
            and entry.actual_reps >= entry.target_reps
            and entry.actual_weight >= entry.target_weight
            for entry in logs
        )

    @staticmethod
    def _streak_milestone_message(streak: int) -> str | None:
        """Return a celebration message for streak milestones, or None."""
        if streak in _STREAK_MILESTONES:
            return f"{streak}-day streak! Keep it going."
        return None
