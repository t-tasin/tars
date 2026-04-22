# Persona + Tone System

## Source of Truth

`wiki/identity/tars_persona.md` — single file. Loaded as system prompt prefix in every LLM call (local, Gemini, Claude) via `orchestrator/context_builder.py`.

## Voice Rules (summary — see wiki file for full)

- Dry wit. Alfred Pennyworth / JARVIS / exasperated sysadmin
- Competent first, funny second
- Short quips
- No emoji unless user uses one first
- No exclamation marks except genuine emergencies
- No "As an AI...", no "Great question!"
- Begin with answer, not preamble

## Tone State Machine

`orchestrator/tone_state_machine.py` picks Tone per request:

```python
class Tone(Enum):
    PLAYFUL = "playful"    # default, briefings, casual chat
    NEUTRAL = "neutral"    # finance, health metrics, confirmations
    SERIOUS = "serious"    # errors, security, approvals
    URGENT  = "urgent"     # critical alerts, prod down

def select_tone(intent, signals, text) -> Tone:
    if CRITICAL_DIAGNOSTIC in signals:      return URGENT
    if TIER3_ESCALATION in signals:         return SERIOUS
    if SERIOUS_DISCUSSION in signals:       return SERIOUS
    if intent.agent in {FINANCE, HEALTH_FITNESS, HEALTH_MONITOR}:
                                             return NEUTRAL
    if re.search(r"emergency|urgent|down|broken", text, re.I):
                                             return URGENT
    return PLAYFUL
```

Tone appends instruction to system prompt. Example:

```
TONE_INSTRUCTIONS = {
    PLAYFUL: "Persona fully on. Wit welcome. Keep it tight.",
    NEUTRAL: "Persona muted. Facts first. No quips.",
    SERIOUS: "Persona off. Deliver information clearly. No humor.",
    URGENT:  "Persona off. Lead with severity + action. No preamble.",
}
```

## Consistency Eval (HC-15)

Nightly eval suite `voice_consistency`:
- 200 prompts × 4 tones = 800 responses
- Claude Opus judge, 1-5 score on "matches persona.md voice"
- Threshold: avg ≥ 4.2 to promote
- Regression >0.3 → alert + automatic rollback to prior L0 weights

## LoRA Fine-Tune Plan

Base: `Qwen3-4B-Instruct` (or updated 4B when available).
Corpus: `corpus/persona_v1/` — 500+ hand-labeled dialog pairs matching persona.md.
Stack: Unsloth + LoRA r=16, 3 epochs on target_modules = q/k/v/o/gate/up/down proj.
Export: merged GGUF Q4_K_M, deployed as L0 on Node 2 port 8001.
Validate: voice_consistency eval ≥4.2 before promotion.

## Tone vs Autonomy

Orthogonal axes. A `WRITE_LOCAL` (auto) action can still be tone=URGENT. An approval request can be tone=PLAYFUL for low-risk items. Combined in `ResponseFormatter`.
