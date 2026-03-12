# Workout Tracker Agent — Design Spec

**Date:** 2026-03-12
**Status:** Draft
**Agent:** `workout_tracker` (new, extends `BaseAgent`, `agent_type = "workout_tracker"`)
**Model:** Gemini Flash
**Tier:** Tier 1 (autonomous — internal data writes only)

---

## Overview

A dedicated workout tracking agent for T.A.R.S. that manages workout splits, enforces progressive overload, and holds the user accountable through streak tracking, skip memory, and coach-personality nudges.

The user defines a workout split (e.g., Push/Pull/Legs/Upper/Lower/Rest/Rest) and seeds initial weights for each exercise. T.A.R.S. owns the progression from there — prescribing weights each session, collecting set-by-set logs via voice (primary) or iOS app (fallback), and advancing weights when all sets hit target reps.

## Requirements Traceability

Extends the Health & Fitness Agent scope from Requirements v2.1 Section 7.2.13:
- **Gym reminders**: "You haven't worked out in 3 days" → now a full accountability system with skip memory
- **Calendar-aware**: reads workout times from Apple Calendar, adapts to schedule changes
- **Trend tracking**: full historical workout logs for progressive overload graphs

New capabilities not in original requirements (user-requested):
- Workout split management with custom rotation
- Progressive overload engine with automatic weight progression
- Set-by-set logging via voice and iOS app
- Mandatory skip reasoning with memory-based accountability
- Streak tracking that respects rest days

## Data Model

### `workout_splits`

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID PK` | `DEFAULT gen_random_uuid()` |
| `name` | `VARCHAR(100) NOT NULL` | e.g., "Push/Pull/Legs" |
| `rotation_days` | `JSONB NOT NULL` | e.g., `["push", "pull", "legs", "upper", "lower", "rest", "rest"]` |
| `active` | `BOOLEAN NOT NULL DEFAULT false` | Only one split active at a time |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | Auto-update trigger |

**Indexes:**
- `idx_splits_active ON workout_splits(active) WHERE active = true` (partial)

**Constraint:** Application-enforced single active split (deactivate old before activating new). Splits are soft-deactivated, never deleted — historical sessions and logs are always retained. All foreign keys use `ON DELETE RESTRICT` to prevent accidental data loss.

### `workout_exercises`

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID PK` | `DEFAULT gen_random_uuid()` |
| `split_id` | `UUID NOT NULL FK → workout_splits(id)` | |
| `day_name` | `VARCHAR(30) NOT NULL` | e.g., "push", "pull", "legs" |
| `exercise_name` | `VARCHAR(100) NOT NULL` | e.g., "Dumbbell Curl" |
| `target_sets` | `INTEGER NOT NULL` | e.g., 3 |
| `target_reps` | `INTEGER NOT NULL` | e.g., 10 |
| `current_weight` | `NUMERIC(8, 2) NOT NULL` | Current prescribed weight |
| `weight_unit` | `VARCHAR(5) NOT NULL DEFAULT 'lbs'` | "lbs" or "kg" |
| `weight_increment` | `NUMERIC(8, 2) NOT NULL DEFAULT 2.5` | How much to increase on progression |
| `order_index` | `INTEGER NOT NULL DEFAULT 0` | Display order within the day |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | Auto-update trigger |

**Indexes:**
- `idx_exercises_split_day ON workout_exercises(split_id, day_name)`

### `workout_sessions`

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID PK` | `DEFAULT gen_random_uuid()` |
| `split_id` | `UUID NOT NULL FK → workout_splits(id)` | |
| `day_name` | `VARCHAR(30) NOT NULL` | e.g., "push" |
| `rotation_index` | `INTEGER NOT NULL` | Position in rotation (0-based), for tracking where we are in the cycle |
| `scheduled_at` | `TIMESTAMPTZ` | From calendar, nullable if unscheduled |
| `status` | `workout_session_status NOT NULL DEFAULT 'pending'` | PostgreSQL ENUM: "pending", "active", "completed", "skipped". Mapped to `WorkoutSessionStatus` StrEnum in `shared/constants.py`. |
| `skip_reason` | `TEXT` | Mandatory if status = "skipped" |
| `started_at` | `TIMESTAMPTZ` | When user tapped Start |
| `completed_at` | `TIMESTAMPTZ` | When session finished |
| `notes` | `TEXT` | Optional user notes |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | Auto-update trigger |

**Indexes:**
- `idx_sessions_status ON workout_sessions(status) WHERE status = 'pending'` (partial)
- `idx_sessions_date ON workout_sessions(created_at DESC)`
- `idx_sessions_split ON workout_sessions(split_id, created_at DESC)`

### `workout_logs`

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID PK` | `DEFAULT gen_random_uuid()` |
| `session_id` | `UUID NOT NULL FK → workout_sessions(id)` | |
| `exercise_id` | `UUID NOT NULL FK → workout_exercises(id)` | |
| `set_number` | `INTEGER NOT NULL` | 1-based |
| `target_reps` | `INTEGER NOT NULL` | Snapshot of target at time of session |
| `target_weight` | `NUMERIC(8, 2) NOT NULL` | Snapshot of target at time of session |
| `actual_reps` | `INTEGER` | Nullable until logged |
| `actual_weight` | `NUMERIC(8, 2)` | Nullable until logged |
| `logged_at` | `TIMESTAMPTZ` | When user reported this set |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | Auto-update trigger |

**Indexes:**
- `idx_logs_session ON workout_logs(session_id)`
- `idx_logs_exercise_date ON workout_logs(exercise_id, created_at DESC)` (for progressive overload history/charts)

**Design note:** `target_reps` and `target_weight` are snapshotted from `workout_exercises` at session creation time. This preserves the exact prescription for historical accuracy, even if the user later modifies the split.

## Progressive Overload Engine

Runs after each session completes. For each exercise in the session:

1. Query all `workout_logs` rows for that exercise in the completed session
2. Check: did every set achieve `actual_reps >= target_reps` at `actual_weight >= target_weight`?
   - **Yes** → set `workout_exercises.current_weight += weight_increment`
   - **No** → no change, same weight next time
3. Two-outcome system. No deloading.

## Accountability System

### Streak Tracking

- Streak counts consecutive **adherence to the rotation**, not just gym days
- Rest days in the rotation are automatically "completed" — they don't break the streak
- Only skipping a workout day (status = "skipped") breaks the streak
- Streak milestones celebrated at 7, 14, 30, 60, 90 days

### Skip Memory

- `workout_sessions.skip_reason` is mandatory when status = "skipped"
- When user attempts to skip, T.A.R.S. queries recent skip history (last 30 days)
- Gemini Flash generates a pushback message using past skip reasons as context
- Example: "Last time you skipped legs you said 'too tired from work.' That was 3 days ago — you've had rest."

### Inactivity Nudges

- Scheduled workout time passes with no "Start" action → +30 min: first nudge via APNs push
- +60 min: second nudge, more assertive
- +90 min: auto-mark as skipped with reason "no response", streak broken, logged for future accountability

### Morning Briefing Integration

- The briefing agent reads today's calendar for workout events
- Includes in briefing: "Gym at 6 PM — it's push day"
- No workout details in briefing; full details come at reminder time

## Interaction Flow

### Workout Time Reminder

1. Scheduler detects upcoming workout from calendar (fires at scheduled time)
2. Sends APNs push with two actions: **Start** and **Skip**
3. Start → opens iOS workout session screen, session status → "active"
4. Skip → opens mandatory reason text field, then submits

### During Active Session

**Voice (primary):**
- User speaks: "First set bench press done, 10 reps 135 pounds"
- Orchestrator routes to workout_tracker agent (intent keywords match)
- Agent delegates to Gemini Flash for natural language → structured log parsing
- Ambiguity triggers follow-up: "Was that set 2 of Bench Press at 135lbs?"
- Agent writes to `workout_logs`

**iOS App (fallback):**
- Workout session screen shows exercises as cards
- Tap exercise → expand to see sets with pre-filled targets
- Tap a set → number input for actual reps and weight
- Writes to `workout_logs` via REST API

### Session Completion

- Auto-completes when all sets for all exercises are logged
- Or manual: user says "I'm done" / taps Finish button (remaining unlogged sets stay null)
- Progressive overload engine runs
- Summary delivered: "Push day done. 6/6 exercises. Bench Press → 140lbs next time. Streak: 15 days."

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/v1/workout/splits` | Create a split with exercises and seed weights |
| `GET` | `/api/v1/workout/splits/active` | Get active split with all exercises |
| `PUT` | `/api/v1/workout/splits/{id}` | Update split (exercises, rotation) |
| `GET` | `/api/v1/workout/sessions/today` | Today's session with exercises and targets |
| `POST` | `/api/v1/workout/sessions/{id}/start` | Mark session active |
| `POST` | `/api/v1/workout/sessions/{id}/skip` | Skip with mandatory reason |
| `POST` | `/api/v1/workout/sessions/{id}/complete` | End session, run progressive overload |
| `POST` | `/api/v1/workout/logs` | Log a set |
| `GET` | `/api/v1/workout/history` | Historical logs (filterable by exercise, date range) |
| `GET` | `/api/v1/workout/streak` | Current streak + recent skip history |

All endpoints require auth (`verify_auth` dependency). All writes logged to `audit_log` per HC-08.

### Key Request/Response Schemas

**`POST /api/v1/workout/splits`** (create split):
```python
class CreateSplitRequest(BaseModel):
    name: str                          # "Push/Pull/Legs"
    rotation_days: list[str]           # ["push", "pull", "legs", "upper", "lower", "rest", "rest"]
    exercises: list[CreateExercise]    # Nested exercises per day

class CreateExercise(BaseModel):
    day_name: str                      # "push"
    exercise_name: str                 # "Bench Press"
    target_sets: int                   # 3
    target_reps: int                   # 10
    current_weight: float              # 135.0 (seed weight)
    weight_unit: str = "lbs"           # "lbs" or "kg"
    weight_increment: float = 2.5     # progression step
```

**`POST /api/v1/workout/logs`** (log a set):
```python
class LogSetRequest(BaseModel):
    session_id: UUID
    exercise_id: UUID
    set_number: int
    actual_reps: int
    actual_weight: float
```

**`POST /api/v1/workout/sessions/{id}/skip`** (skip with reason):
```python
class SkipSessionRequest(BaseModel):
    reason: str  # Mandatory, non-empty
```

## iOS App Changes

### New Screens

1. **Workout Session Screen** — displayed on Start action. Exercise cards with target sets/reps/weight. Tap to expand and log each set. Finish button at bottom.

2. **Split Setup Screen** — define split name, rotation days (including rest), add exercises per day with seed weight, sets, reps, and increment values.

### Push Notification Actions

New APNs action category `WORKOUT_REMINDER` with:
- `START_WORKOUT` action (opens workout session screen)
- `SKIP_WORKOUT` action (opens skip reason input)

## Orchestrator Integration

### Intent Classifier

New pattern in `intent_classifier.py`:
```
re.compile(r"set\s+\d|reps?\b.*\b(done|complete)|workout\s+(split|routine|log)|progressive\s+overload", re.IGNORECASE)
```
Routes to `IntentType.WORKOUT_TRACKER`.

**Rule ordering:** The workout_tracker pattern must be inserted **before** the health_fitness pattern in `_INTENT_RULES` (first-match-wins). The existing health_fitness pattern must be narrowed from `sleep|steps|workout|gym|exercise|health|fitness` to `sleep|steps|health|fitness` — removing `workout`, `gym`, and `exercise` since those now belong to the workout tracker domain.

Messages like "how did I sleep?" still route to health_fitness. Messages like "log my workout" or "I'm at the gym" route to workout_tracker.

**Slash command:** Add `/workout` to `_COMMAND_MAP` → `Intent(agent=IntentType.WORKOUT_TRACKER)` for quick access to today's session. Consistent with existing `/briefing`, `/jobs`, `/outfit` commands.

### Model Router

```python
IntentType.WORKOUT_TRACKER: ModelRoute(model=ModelName.GEMINI_FLASH, node="node1")
```

### Scheduler Jobs

Two new jobs in `scheduler/jobs.py`:
1. **`create_daily_workout_session`** — runs at 5:30 AM via cron trigger. Reads today's calendar for workout events, creates a `workout_sessions` row with the correct rotation day and `scheduled_at` time. If today is a rest day in the rotation, no session is created.
2. **`workout_reminder_poll`** — runs every 5 minutes via interval trigger. Checks for any `pending` sessions where `scheduled_at` has arrived. On first match: sends APNs push with Start/Skip actions. At +30 min: sends first inactivity nudge. At +60 min: sends second nudge (more assertive). At +90 min: auto-marks as skipped with reason "no response". This polling approach is consistent with the existing `expire_approvals_job` pattern and avoids dynamically scheduling one-off jobs.

**Note:** The polling job uses the `scheduled_at` timestamp on the session to compute elapsed time, so all timing is relative to the calendar event — no dynamic APScheduler `DateTrigger` jobs needed.

## Files to Create/Modify

### New Files
- `backend/src/agents/workout_tracker.py` — agent with progressive overload engine, accountability logic, voice log parsing
- `backend/src/api/workout.py` — REST endpoints
- `backend/src/db/repositories/workout_repository.py` — data access layer
- `backend/tests/test_agents/test_workout_tracker.py` — unit tests
- `backend/tests/test_api/test_workout.py` — API tests
- `ios/TARS/TARS/Views/Workout/WorkoutSessionView.swift` — session screen
- `ios/TARS/TARS/Views/Workout/SplitSetupView.swift` — split setup screen
- `ios/TARS/TARS/ViewModels/WorkoutViewModel.swift` — view model
- Alembic migration for new tables

### Modified Files
- `backend/src/api/router.py` — register workout router
- `backend/src/orchestrator/intent_classifier.py` — add workout_tracker pattern
- `backend/src/orchestrator/model_router.py` — add workout_tracker route
- `backend/src/scheduler/jobs.py` — add 3 new scheduler jobs
- `shared/constants.py` — add `IntentType.WORKOUT_TRACKER`, `WorkoutSessionStatus` StrEnum, and `WORKOUT_TRACKER` entry in `AGENT_MODEL_MAP`
- `ios/TARS/TARS/Services/APIClient.swift` — add workout endpoints
- `backend/src/db/models.py` — add ORM models for 4 new tables

## Coordination with Health & Fitness Agent

When an active workout split exists, the health_fitness agent's calendar-based gym suggestions (`_get_calendar_context` free slot logic) should be suppressed — the workout_tracker provides more specific information ("it's push day" vs. "you have a free slot for a workout"). The health_fitness agent can still cross-reference `workout_sessions` for enriched summaries (e.g., "Burned 450 cal at the gym + walked 8,000 steps today").

## Out of Scope (Future)

- Progressive overload dashboard/graphs (data is stored and queryable, UI is future work)
- Exercise video/form guidance
- AI-generated workout plans (user defines their own split)
- Integration with gym equipment or wearables beyond Apple Watch
- Nutrition tracking tied to workout performance
