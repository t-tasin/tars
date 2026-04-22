# Testing

## Run
```bash
cd backend && .venv/bin/python -m pytest tests/ -v
```

`pyproject.toml` sets `pythonpath = ["src", ".."]` so both `orchestrator.*` and `shared.*` resolve. Always use `.venv/bin/python`.

## Categories

| Category | Location | What it covers |
|---|---|---|
| Unit | `tests/test_*.py` | Each agent, intent classifier, signal detector, model router, approval manager, autonomy budget, tone state machine, curator |
| Integration | `tests/integration/` | Integration clients w/ mocked externals, Qdrant via testcontainers, Redis via fakeredis |
| API | `tests/test_api/` | Every REST endpoint via TestClient |
| E2E | `tests/e2e/` | Full message pipeline, distributed job round-trip, wake-word → TTS |
| Eval | `tests/eval/` | Nightly harness — 6 suites (see below) |

## Eval Suites (Nightly)

Run via `scripts/eval_nightly.py` at 02:00:

| Suite | Count | Metric | Threshold |
|---|---|---|---|
| intent_classifier | 200 labeled | accuracy | ≥0.95 |
| email_classifier | 500 × 4 tiers | F1 per class | ≥0.85 |
| briefing | 50 golden | Claude Opus judge 1-5 | avg ≥4.0 |
| voice_consistency | 200 × 4 tones | Claude Opus judge 1-5 | avg ≥4.2 (HC-15) |
| routing_precision | 100 w/ ideal tier | accuracy | ≥0.9 |
| wiki_retrieval | 50 queries × 8 top-k | recall@8 | ≥0.85 |

Results → `evals` table. Grafana panel "Eval Health". Regression >5% → Apprise alert.

## Mocking Rules

- **Unit**: mock ALL external APIs
- **Integration**: real Qdrant + Redis via testcontainers OK; mock Gemini/Claude
- **E2E**: real local llama.cpp OK if Node 2 available in CI; otherwise mock

## Coverage
- Target: 70%+ branch
- Check: `pytest --cov=src --cov-report=term-missing --cov-report=html`
- CI artifact: uploaded every PR

## CI
- `.github/workflows/test.yml` — runs on every PR
- `.github/workflows/lint.yml` — ruff check + format
- `.github/workflows/build-and-push.yml` — GHCR images on main merge

## Before Marking TESTED (in FEATURES.md)
1. Unit test(s) exist
2. Integration test if touches DB/Redis/Qdrant/external API
3. At least one golden case added to relevant eval suite
4. 24h+ soak in dev with no regression
5. Status updated, evidence cited
