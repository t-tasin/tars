# A Day in the Life of Tasin, Assisted by T.A.R.S.

_Wooster, Ohio. Wednesday, May 7, 2026. Tasin is mid-job-search,
currently contracting part-time, preparing portfolio interviews._

> This document is the product spec. If a feature doesn't serve a moment
> in this day, question whether it should exist. If a moment in this day
> isn't supported by code, that's a gap.

---

## 06:42  Ambient wake

Tasin's iPhone alarm goes off. HealthKit fires a `world:healthkit` event to
Node 1: sleep 7h 12m, HRV recovered, REM 19% (normal). TARS's TriggerEngine
matches `sleep_score >= 75 AND first_motion_detected` → dispatches
`morning_routine_start`.

No message sent. Just prep. Local Qwen3-30B-A3B spins up on Node 2 (warm
from last night). ContextBuilder pre-fetches: CalDAV (3 events today),
Gmail (26 new since 22:00), weather, yesterday's EOD summary, today's
top 3 wiki-retrieved goals.

## 06:58  The ambient brief

Tasin opens the iOS app. Live Activity is already populated.

TARS (via Gemini 2.5 Pro, briefing tier):

  "07:02. Three meetings today, first at 10:30 — that's the AtlasDesk
   follow-up you've been rescheduling. Weather says 71 and sunny through
   3pm, then a 40% rain chance. Your inbox has 26 new; I triaged them —
   one from Prof. Sadigh that actually matters, the rest are LinkedIn
   pretending to be people.

   Two things for the list: your car is filthy (last wash per calendar
   was March 14, it's been 54 days), and this sun won't last. Second,
   your Boss jeans wear pattern says you've done six washes — warranty
   covers a repair voucher if you want me to file one.

   Coffee's yours. I'll be here."

Tasin: "how filthy we talking"

TARS (local Qwen3-4B-tars, L0, 280ms):

  "Didn't run a visual. Said 54 days based on your own 'car wash' calendar
   event cadence from last two years (avg 21). Extrapolation, not
   inspection. Want me to stop guessing and shut up about it?"

Tasin: "fine, add wash to today"

TARS: Creates calendar event 14:00 at Shine Car Wash (from his past 5
preferred-location pattern in wiki). Auto-approved (Tier 1, local
calendar write via WRITE_LOCAL budget).

## 07:40  Outfit

Tasin in bathroom. iPhone Shortcut `/outfit` triggers FashionAgent.

FashionAgent does:
  1. Retrieve weather → 71°F sunny
  2. Query Postgres `calendar_events` today → one in-person meeting (not
     video), venue = coffee shop, formality_score = 2/5
  3. Query Qdrant `wardrobe` collection w/ filter
     `weather:warm AND formality<=3 AND last_worn > 14d`
  4. Query wiki `preferences.md` for "Tasin likes contrast in spring"
  5. Pass top-8 candidates + context to Gemini Vision (has images)
  6. Gemini Vision composes 3 outfit grids

APNs push, Watch approval card:

  Option A: pink oxford + khaki cargo + white Sambas
            "Spring contrast play. Last worn 31 days ago."
  Option B: cream henley + dark denim + boots
            "Safe. Kind of bored with this one, honestly."
  Option C: navy polo + linen pants + loafers
            "Bumped formality in case meeting turns into something."

Tasin taps A on watch. Watch posts approval back. TARS logs
`wardrobe_outfits` row w/ worn_date, writes world_state event.

## 08:30  Morning deep-work block

Tasin opens his laptop. Hammerspoon daemon notices Xcode becomes active.
Publishes `world:mac:focus app=Xcode`. TriggerEngine matches
`focus_mode_enter` → TARS mutes Telegram for 90min, holds non-critical
notifications, keeps only tier-3 escalations live.

At 09:15, a GitHub webhook fires: AtlasDesk CI failure on main. TARS
classifies as CRITICAL_DIAGNOSTIC. Escalation signal raised. Router
skips local, goes to Claude Sonnet (serious tone enforced).

Claude Sonnet spawned w/ `mcp_profile="diagnostics"` — has Postgres +
Brave Search access. Pulls last 50 Loki log lines via grafana_client.
Identifies: OOM in worker pod, same root cause as April 19 (in wiki
`decisions/2026-04-19-atlasdesk-oom.md`).

APNs push (URGENT tone, no humor):

  "AtlasDesk worker OOMed at 09:14. Same pattern as Apr 19 —
   image processing batch size not capped. Two options:
   A) Roll back to e85c401 (last good)
   B) Apply the batch-size patch we drafted 18 days ago but never
      shipped (draft in your Notion: 'atlasdesk-batch-fix')
   Recommending B. Needs your approval."

Tasin taps approve on watch — doesn't leave Xcode.
TARS routes to CodingAgent, dispatches job to Node 2 worker via
`tars:jobs:queue`. Worker clones repo in Docker sandbox, applies
patch, runs tests in container, opens PR via github MCP.
PR URL back to Tasin in 2m 40s. Tasin reviews from phone, merges.

Total hands-on-keyboard time: 14 seconds.

## 12:15  Lunch, ambient chat

Tasin walking to lunch. Says out loud: "Hey TARS."

Porcupine detects. USB mic streams. Whisper transcribes:
"remind me to email my advisor about thesis."

Local Qwen3-30B-A3B runs. Intent: COMMUNICATION, action=draft_email,
entity=advisor (resolved via wiki/relationships/professors/sadigh.md).

TARS drafts email using LoRA-fine-tuned Qwen3-4B-tars (Tasin's writing
voice), pulls thread context from Gmail last 30d w/ Sadigh, references
progress notes in wiki/projects/thesis.md.

TARS (AirPlay to AirPods):

  "Drafted. Ten sentences. Asking for a 30-min check-in next week,
   specifying the two blockers on chapter three. Tone matches how you
   usually write him, I checked against your last three. Want me to
   push it to your phone for review, or sit on it until you're back?"

Tasin: "push it, I'll look over lunch."

TARS creates Tier-3 approval (email to professor = tier3_escalation).
APNs push with full preview. Tasin reviews over lunch, edits one line,
approves. Gmail API sends. Audit log entry.

## 14:00  Car wash

Calendar reminder fires. Siri Shortcut triggers TARS.

TARS (via Watch haptic): "Car wash in 10. Shine is at usual spot, 8 min
drive."

No LLM called. Pure cron + local calendar. Free.

## 16:30  Job search batch

TARS's scheduled `job_search` cron. Scrapes LinkedIn + Greenhouse + YC
+ Google Jobs for new matches against Tasin's wiki/projects/tars.md
skill vector. 89 new listings scraped. Local Qwen3-30B-A3B (L1) does
initial filter — 67 fail the hard requirements. 22 pass.

For the 22, Gemini 2.5 Flash does detailed eval with grounded search
(checking company latest funding, layoffs, Glassdoor). 6 pass that gate.

These 6 get written to Notion DB + summary card pushed to iOS:

  "6 candidates worth a look today. Top pick: Figure AI, applied ML
   engineer, 160-200 base + equity. Your 7-of-9 stack match and they
   shipped humanoid robots last month so the money is real. I wrote
   the cover letter skeleton — your voice, their JD — in
   outputs/covers/figure-2026-05-07.md. Review when ready."

TARS did NOT auto-apply. Job applications are `WRITE_WORLD`, need
approval. The cover letter draft is `WRITE_LOCAL`, auto-fine.

## 18:00  Location trigger

iPhone Shortcut fires `location.arrived_home`. TriggerEngine matches
`home + evening + no_meeting_within_2h` → `evening_wind_down`.

TARS writes a private journal entry (never pushed, just logged in wiki
`decisions/YYYY-MM-DD-log.md`). It summarizes what it did that day,
what went well, what Tasin ignored. This becomes training corpus for
next LoRA iteration.

At 19:30 Tasin notices living room's warm, asks: "what's the house
vibe?"

TARS (local Qwen3-30B-A3B, playful tone):

  "Calm. Spotify says you're on the lo-fi rotation. Mac's been on
   Figma for 40 minutes which usually means you're designing something
   you won't ship. You skipped the gym. We don't need to talk about
   that. What did you want to know actually, because that wasn't a
   real question."

Tasin: "lol, fair. what's for dinner"

TARS: pulls fridge inventory (last photo logged Sunday via iOS upload,
Gemini Vision catalogued). Cross-references wiki/identity/dietary.md
(no pork, halal, currently cutting carbs). Suggests: "Salmon + asparagus
you have. Takes 18 min. I don't cook, but I can queue the Spotify
cooking playlist if you start."

## 22:30  EOD

Cron triggers EODSummaryAgent. Gemini 2.5 Pro composes one Notion page:
  - Meetings attended / skipped
  - Code: 12 commits, 1 PR merged, AtlasDesk stabilized
  - Fitness: skipped gym (flagged)
  - Calories: read from MyFitnessPal export if enabled
  - Wiki updates proposed: 3 (CuratorAgent)
  - Emails: 47 triaged, 2 approved for send, 45 archived/labeled
  - Approvals today: 4 (all handled <3 min avg)
  - Model usage: 73% local, 19% Gemini Flash, 6% Gemini Pro, 2% Claude
  - Cost today: $0.31
  - Power draw today: ~387Wh
  - Power cost today: $0.06

APNs push with summary. Tasin reviews on Watch, approves the 3
proposed wiki updates. CuratorAgent commits wiki/ changes to git,
re-embeds deltas into Qdrant.

## 23:55  Last thing

Tasin plugs phone in. Location stable, motion quiet, HealthKit detecting
sleep soon.

TARS mutes itself for the night, drops L2 model from RAM (keeps L1
warm for wake word). Node 2 idle draw falls to 12W.

Dashboard public feed shows: "T.A.R.S. is sleeping. Wake word armed.
Next cron: 06:00 morning data prep." Softens the 3D viz to dim pulse.

End of day. Tasin didn't type a prompt. He talked to a system.

---

## Feature Inventory from This Day

For each moment, the feature IDs in `docs/FEATURES.md` that must be SHIPPED:

- 06:42 Ambient wake — P4-01 (sensor base), P4-06 (HealthKit), P4-10 (TriggerEngine)
- 06:58 Brief — existing BriefingAgent, P3-09 (wiki retrieval), P5-02 (persona)
- 07:40 Outfit — existing FashionAgent, P1-01..04 (queue fix), P3-01 (Qdrant wardrobe)
- 08:30 Deep-work — P4-03 (mac sensor), P4-23 (focus_mode trigger)
- 09:15 Crash diagnosis — existing HealthMonitorAgent, P2-08 (signal detector), P4-22 (atlasdesk_watchdog)
- 12:15 Voice draft — existing wake-word, P5-11 (LoRA deployed), P3-09 (wiki retrieval), existing CommunicationAgent
- 14:00 Cron — existing Scheduler
- 16:30 Job search — existing JobSearchAgent, P2-09 (router), P3-09 (wiki)
- 18:00 Evening — P4-15 (evening_wind_down), P4-02 (location)
- 19:30 Vibe chat — P2-06 (local client), P5-05 (tone), P4-04 (spotify), P4-03 (mac)
- 22:30 EOD — existing EODSummaryAgent, CuratorAgent (new), P3-10 (wiki_proposals)
- 23:55 Sleep — systemd model eviction, public dashboard (P3-15, P6-01)

Every feature above MUST exist before the day-in-life works end-to-end.
