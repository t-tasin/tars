# T.A.R.S. — Tasin's Autonomous Resource System

A personal AI assistant platform that runs 24/7 on two HP Z2 Mini G3 workstations, providing intelligent life management through a multi-model AI architecture.

## Architecture

- **Node 1 (Brain)**: FastAPI backend, orchestrator, PostgreSQL, integrations
- **Node 2 (Muscle)**: Redis queue, ChromaDB vector store, sandboxed job workers

## Clients

- iOS App (SwiftUI) — primary interface
- Telegram Bot — fallback interface
- Apple Watch — notifications + quick approvals
- HomePod Mini — voice output via AirPlay
- "Hey TARS" wake word — USB mic on Node 1

## Quick Start

```bash
cp .env.example .env
# Fill in your environment variables
make build
make deploy-node1
make deploy-node2
```

## Development

```bash
cd backend && python -m pytest tests/ -v
make lint
make test
```
