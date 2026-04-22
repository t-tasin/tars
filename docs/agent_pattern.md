# Agent Development Pattern

## Create New Agent

1. `backend/src/agents/<agent_name>.py`
2. Extend `BaseAgent`:

```python
from __future__ import annotations

from agents.base import BaseAgent, AgentResult, AgentContext
from shared.constants import IntentType, AutonomyClass, ModelTier


class YourAgent(BaseAgent):
    """One-line description of what this agent does."""

    AGENT_TYPE = "your_agent"             # must match intent_classifier mapping
    DEFAULT_TIER = ModelTier.L1_BRAIN     # L0_REFLEX | L1_BRAIN | L2_WEB | L3_DEEP | L4_REASONING | L5_ESCALATION
    AUTONOMY_CLASS = AutonomyClass.WRITE_LOCAL  # REQUIRED — no default

    async def execute(self, context: AgentContext) -> AgentResult:
        """Main logic.

        context.wiki_chunks — top-k retrieved wiki snippets (already built)
        context.calendar, context.emails, context.weather — as applicable
        """
        # 1. Read scoped context
        # 2. Call model via self.local | self.gemini | self.claude
        # 3. Return structured AgentResult

        response = await self.local(
            prompt=context.user_message,
            system=self._build_system_prompt(context),
            tier=self.DEFAULT_TIER,
        )

        return AgentResult(
            content={"result": response.text},
            text=response.text,
            model=response.model,
            autonomy_class=self.AUTONOMY_CLASS,

            # If side-effect-producing:
            # has_side_effects=True,
            # action_type="send_email",       # maps to TIER_MAP in ApprovalManager
            # approval_title="Send email to ...",
            # preview={...},
        )
```

3. Register in `agents/__init__.py` + `main.py::_register_agents`
4. Register in `orchestrator/intent_classifier.py`:
   ```python
   re.compile(r"your|keywords", re.I): Intent(agent=IntentType.YOUR_AGENT)
   ```
5. Register default tier in `orchestrator/model_router.py::AGENT_MODEL_MAP`
6. If scheduled, add to `scheduler/jobs.py`
7. Tests in `tests/test_agents/test_<agent_name>.py` — include:
   - Happy path
   - Missing context (validate_context failure)
   - Model unavailable (fallback)
   - `test_<agent>_autonomy_class` — asserts correct class
8. Feature row in `docs/FEATURES.md` — status `PLANNED` before PR, transition through on merges

## Required `BaseAgent` Interface

```python
class BaseAgent(ABC):
    AGENT_TYPE: str              # unique id
    DEFAULT_TIER: ModelTier
    AUTONOMY_CLASS: AutonomyClass

    @abstractmethod
    async def execute(self, context: AgentContext) -> AgentResult: ...

    async def validate_context(self, context: AgentContext) -> bool:
        """Override to check required context fields. Return False to skip agent."""
        return True
```

## AgentResult Schema

```python
@dataclass
class AgentResult:
    success: bool = True
    text: str = ""                           # human-readable response
    content: dict[str, Any] = field(default_factory=dict)
    model: str = "local"
    autonomy_class: AutonomyClass            # REQUIRED
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    # For side-effect actions:
    has_side_effects: bool = False
    action_type: str | None = None           # maps to TIER_MAP
    approval_title: str | None = None
    preview: dict[str, Any] | None = None
```

## Approval Action Type → Tier Map

```python
TIER_MAP = {
    "send_email":       "tier2_approval",
    "create_event":     "tier2_approval",
    "archive_emails":   "tier2_approval",
    "create_notion":    "tier2_approval",
    "create_pr":        "tier2_approval",
    "apply_job":        "tier2_approval",
    "draft_reminder":   "tier1_auto",        # WRITE_SELF

    "email_professor":  "tier3_escalation",
    "push_production":  "tier3_escalation",
    "delete_data":      "tier3_escalation",
    "modify_infra":     "tier3_escalation",
}
```

## How the Orchestrator Calls You

```python
orchestrator.process_message(text, source)
  → classify → route → build_context → execute(ctx) → track_usage → approval?
  → format → save → audit_log → response
```

Your agent sees only `AgentContext`. Do not call Redis, Postgres, or external APIs directly — use the provided clients (integration layer, Qdrant client, etc.) injected via dependencies.
