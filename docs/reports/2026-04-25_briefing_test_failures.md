# Pre-existing CI failures: `test_integration_briefing` (2 tests)

**Date:** 2026-04-25
**Surfaced on:** PRs #24, #25 (also red on #23 `dc4d9b4` — pre-dates Phase 3.5).
**Status:** Not introduced by Phase 3.5 work. Both failures reproduce on `main` with no Phase 3.5 changes applied.
**Severity:** Medium — gates CI green-light but does not block deploys (other PRs have merged through it).

---

## Failing tests

| # | Test | Assertion | Outcome |
|---|------|-----------|---------|
| 1 | `tests/test_integration_briefing.py::TestBriefingAgentPipeline::test_full_briefing_pipeline` | `assert len(briefing_rows) >= 1` | `assert 0 >= 1` |
| 2 | `tests/test_integration_briefing.py::TestOrchestratorBriefingPipeline::test_orchestrator_routes_briefing_command` | `assert len(briefings) == 1` | `assert 0 == 1` |

Both expect a `briefings` row to land after `BriefingAgent.execute()`. Neither row is created → both fail.

## Root cause chain

The `briefings` row never lands because `BriefingAgent.execute()` short-circuits before persistence. Two upstream errors fire during the pre-fetch phase introduced in Phase 2.5:

1. **`ContextBuilder._pre_fetch_email` — `GmailClient` constructor crashes**

   Stack:
   ```
   src/orchestrator/context_builder.py:251  client = GmailClient(...)
   src/integrations/gmail_client.py:66       cred_data = json.loads(base64.b64decode(credentials_json_b64))
   json/__init__.py:346                      raw_decode(...)
   ```
   The CI environment's mocked `gmail_personal_credentials` is `"dGVzdA=="` (`b64("test")`). After base64-decode it's the literal string `"test"`, which is not valid JSON → `json.JSONDecodeError`. The pre-fetch catches the exception, logs, and returns empty.

2. **Gemini client receives an invalid API key in CI**

   ```
   google.genai.errors.ClientError: 400 INVALID_ARGUMENT.
   {'error': {'code': 400, 'message': 'API key not valid. Please pass a valid API key.', ...}}
   ```
   The Gemini composition path fails. With Phase 2.5's `LOCAL_BRAIN`-first design, the local fallback should still produce a narrative — but in this test the narrative path returns empty data, so `BriefingRepository.upsert(...)` is never called.

## Why this is not a Phase 3.5 regression

- Test file `tests/test_integration_briefing.py` last touched 2 commits ago (`9ea501e` initial, `59ede88` ruff). No edits in P3.5-01 or P3.5-02.
- PR #24 (P3.5-01) ran the full suite: **1179 passed, 2 failed** — exactly these two tests. PR #25 (P3.5-02) reproduced the same: **1179 passed, 2 failed**.
- Identical failures appeared on PR #23 `fix(briefing): LocalClient timeout 300s + UPSERT` (commit `dc4d9b4`), which was merged red.
- The Gemini and Gmail-credential issues are CI-environment problems (test settings, not production credentials), not regressions in agent code.

## Suggested follow-up (out of scope for P3.5)

1. **Fix the Gmail mock credential**
   Replace `_MOCK_SETTINGS.gmail_personal_credentials = "dGVzdA=="` in `tests/conftest.py` with a valid base64'd JSON blob (e.g. `base64.b64encode(json.dumps({"token":"x","refresh_token":"y","client_id":"z","client_secret":"w","token_uri":"u"}).encode()).decode()`), or patch `GmailClient.__init__` to accept a missing/malformed cred and return a no-op stub when in tests.
2. **Mock Gemini at the SDK boundary in CI**
   The current test injects a `mock_gemini` AsyncMock into `AgentContext.config`, but `ContextBuilder._pre_fetch_*` constructs its own clients. Either inject the same mock into `ContextBuilder`, or set `gemini_api_key` to a value that bypasses real network calls (e.g. via `respx`/`httpx_mock` for the genai HTTP layer).
3. **Make `BriefingAgent` write the briefings row even when pre-fetch returns empty**
   If "no real data, fall back to local with empty context" is acceptable behaviour (it is, per HC-09), the test should still see a row. Either the agent should persist the fallback narrative, or the test should assert success only when external integrations are healthy.

Each option is a half-day fix. None blocks Phase 3.5-03 (weather sensor) since sensor work doesn't touch the briefing path.

## Phase 3.5 status (for context)

| ID | State | PR |
|----|-------|----|
| P3.5-01 BaseSensor abstract | **MERGED** | #24 (`b1ed4b4`) |
| P3.5-02 world_state migration | **MERGED** | #25 (`ca957a0`) |

Foundation complete. Sensor implementations (P3.5-03..06) are unblocked.
