# How T.A.R.S. Builds the Tasin Wiki

## Principle

Wiki starts hand-seeded (~20 files). Grows automatically from every
interaction. Tasin is the final editor — nothing goes in without approval.
Stateful, versioned, auditable.

## The Loop

```
Every conversation  →  Postgres messages table
Every sensor event  →  Postgres world_state table
Every approval      →  Postgres approvals table
Every agent output  →  Postgres agent_outputs table
Every commit / file change → git hook

          ↓ daily at 23:00

  CuratorAgent (runs on local Qwen3-30B-A3B, L1 Brain)
    - reads yesterday's data
    - extracts: facts, preferences, relationships, decisions,
      patterns, corrections, promises made/kept/broken
    - drafts wiki edit proposals — diff-format against current wiki/

          ↓

  Tasin approval (Tier-2, batched once/day)
    - Tasin reviews in iOS/Telegram: accept / edit / reject each proposal
    - Rejections fed back as negative training signal

          ↓

  Approved edits:
    - committed to wiki/ git repo w/ auto-commit "curator: {summary}"
    - re-chunked, re-embedded (Qwen3-Embedding-0.6B)
    - Qdrant delta-upsert for tasin_wiki collection
    - Postgres wiki_index updated (entity graph)

          ↓

  Feedback loop
    - next day, CuratorAgent reads prior approvals/rejections
    - learns what level of proposal is welcome
```

## What CuratorAgent Extracts

### From conversations
- Preferences ("I don't like mornings" → `wiki/identity/preferences.md`)
- Facts ("my rent is 1200" → `wiki/identity/finance.md`, marked private)
- Goals ("I want to interview at Figure" → `wiki/identity/goals.md`)
- Opinions ("I think Swift is better than Kotlin" → `wiki/identity/opinions.md`)
- Corrections ("no, I don't drink coffee anymore" → overwrite prior fact)
- Relationships ("my friend Alex, we met in CS109" → `wiki/relationships/alex.md`)
- Taste ("I liked that movie Poor Things" → `wiki/identity/culture.md`)

### From sensors
- Routines (leaves home avg 09:14 weekdays → `wiki/routines/commute.md`)
- Patterns (skips gym after <6h sleep → `wiki/identity/health.md` behavioral notes)
- Locations (frequents Stan's Donuts → `wiki/identity/places.md`)
- Music taste (Spotify top tracks → `wiki/identity/culture.md` monthly rollup)

### From actions
- Decisions (chose React over Vue for X project → `wiki/decisions/YYYY-MM-DD.md`)
- Writing samples (sent emails → `wiki/writing/samples/`, used for LoRA)
- Reactions (approved/rejected approvals → `feedback_log` → persona refinement)

### From code
- Stack choices (git log analysis) → `wiki/knowledge/stack.md`
- Interests (repo topics + language dist) → `wiki/identity/goals.md`
- Productivity (commit times, streaks) → `wiki/routines/work.md`

## Schema

```sql
CREATE TABLE wiki_proposals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  target_path TEXT NOT NULL,          -- "wiki/identity/preferences.md"
  operation TEXT NOT NULL,            -- "create" | "append" | "edit" | "delete"
  diff TEXT NOT NULL,                 -- unified diff if edit
  rationale TEXT NOT NULL,            -- why curator proposes this
  evidence JSONB NOT NULL,            -- { "sources": [message_id, event_id, ...] }
  confidence FLOAT NOT NULL,          -- 0-1
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | edited | snoozed
  decided_at TIMESTAMPTZ,
  decided_by TEXT,                    -- "tasin"
  user_edit TEXT                      -- if Tasin edited before approve
);
CREATE INDEX idx_wiki_proposals_status ON wiki_proposals(status, created_at DESC);
```

```sql
CREATE TABLE wiki_index (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_type TEXT NOT NULL,     -- person | project | place | concept | routine | decision
  entity_name TEXT NOT NULL,
  file_path TEXT NOT NULL,
  aliases JSONB DEFAULT '[]'::jsonb,
  last_mentioned_at TIMESTAMPTZ,
  mention_count INT DEFAULT 0,
  relationships JSONB DEFAULT '{}'::jsonb,  -- { "works_with": ["alex_id"], ... }
  UNIQUE (entity_type, entity_name)
);
```

## Proposal Format (what Tasin sees)

```
╔═════════════════════════════════════════════════╗
║ Wiki Proposal — 2026-05-07                      ║
╠═════════════════════════════════════════════════╣
║ File: wiki/identity/preferences.md              ║
║ Operation: APPEND                               ║
║ Confidence: 0.82                                ║
║                                                 ║
║ + ## Coffee (as of May 2026)                    ║
║ + You've moved off coffee — said "I don't drink ║
║ + coffee anymore" on May 3 and have logged zero ║
║ + Spotify coffee-shop visits in 14 days.        ║
║                                                 ║
║ Why: you told me May 3; sensor data confirms.   ║
║ Evidence: message_abc123, world_state_xyz789    ║
║                                                 ║
║ [Approve] [Edit] [Reject] [Snooze]              ║
╚═════════════════════════════════════════════════╝
```

UI delivery:
- iOS app — dedicated "Wiki Proposals" tab
- Telegram — inline keyboard w/ Approve / Edit / Reject
- Watch — summary card for Approve / Reject only (Edit falls back to phone)

## Guardrails (`wiki/.curator-policy.yml`)

```yaml
max_proposals_per_day: 10
min_confidence: 0.6
topic_deny:
  - medical_conditions      # require explicit Tasin mention
  - sexual_preferences
  - religious_beliefs
  - passwords
  - financial_account_numbers
  - ssn_or_id_numbers
max_diff_lines: 40           # no massive rewrites

pii_scrub:
  - credit_card
  - ssn
  - api_keys
  - email_password_pairs
  - mailing_addresses (unless Tasin explicitly logs them)

file_allow:
  - wiki/identity/*
  - wiki/relationships/*
  - wiki/projects/*
  - wiki/writing/*
  - wiki/decisions/*
  - wiki/routines/*
  - wiki/knowledge/*
file_deny:
  - wiki/.curator-policy.yml   # curator can't edit its own rules
  - wiki/identity/credentials  # never
```

## Correction Learning

If Tasin rejects a proposal with a comment ("no, that's wrong, I actually
do still drink coffee, just not in the mornings"), that rejection feeds:

1. New wiki proposal: more precise fact ("only drinks coffee after 10am"
   with evidence)
2. `feedback_log` row — curator learns it drew wrong conclusion
3. Next week's CuratorAgent prompt includes "avoid over-generalizing
   single statements" — self-correction via prompt engineering, not
   retraining

## Bootstrap (Day 1)

Tasin hand-seeds:
```
wiki/
├── identity/
│   ├── bio.md                 # birthdate, roles, timezone
│   ├── goals.md               # short/medium/long term
│   ├── preferences.md         # seeded w/ 20 known facts
│   ├── dietary.md             # halal, no pork, currently cutting carbs
│   ├── health.md              # none critical, glasses prescription
│   └── tars_persona.md        # voice guide — THIS FILE MATTERS MOST
├── relationships/
│   ├── family/
│   │   └── mom.md
│   ├── professors/
│   │   └── sadigh.md
│   └── friends/
│       └── alex.md
├── projects/
│   ├── tars.md                # this
│   └── atlasdesk.md
├── writing/
│   └── voice.md               # tone guidelines for your-voice drafts
├── routines/
│   └── morning.md
└── knowledge/
    └── stack.md               # tools you use + opinions
```

Each file is ~100-300 words to start. CuratorAgent grows them.

## Why This Is the Unique Flex

- Normal assistant: "I'll remember that!" (lies, has no memory mechanism)
- Custom assistant: stores everything in a vector DB (brittle, no audit,
  hallucinates facts from misretrieved chunks)
- TARS: **structured wiki + approval gate + audit trail + self-learning**
  — every fact has a file path, a commit hash, an evidence trail, and
  was approved by Tasin.

Recruiter reaction: "This isn't a toy. This is a personal knowledge
management system that happens to be wired to LLMs."

## Integration Points

- `BriefingAgent` — pulls top-8 relevant wiki chunks for morning context
- `CommunicationAgent` — pulls recipient profile from `relationships/`
  + your voice from `writing/voice.md`
- `FashionAgent` — pulls `preferences.md` seasonal taste notes
- `JobSearchAgent` — pulls `projects/tars.md` for skill vector matching
- `CuratorAgent` — reads everything, proposes updates
- `ContextBuilder` — universal: every AgentContext gets wiki chunks

## Failure Modes + Mitigations

| Failure | Mitigation |
|---|---|
| CuratorAgent hallucinates a preference | Confidence gate (≥0.6) + evidence required + Tasin approval |
| Proposal reveals private info in public dashboard | Dashboard sanitizer strips wiki content entirely — never emitted |
| Wiki git history reveals deleted fact | Wiki repo is separate private remote, never public |
| Corpus drift (curator's tone diverges) | Weekly eval: 20 sample proposals judged by Claude Opus on tone-fit |
| Bulk rejection causes curator collapse | Minimum proposals/day=1 even if low confidence — keep signal |
| Tasin approval fatigue | Daily batch cap 10; auto-snooze low-confidence; ranking |

## Future — Phase 10+

- Wiki semantic search UI on public dashboard (redacted, navigable)
- CuratorAgent confidence calibration via approval rate tracking
- Automated LoRA retraining when `writing/samples/` grows >500 new
- Cross-agent memory: agents can write findings to
  `wiki/knowledge/learned.md` via CuratorAgent proposals
