# Deployment

## Flow
```
Mac (dev) → GitHub push → GH Actions CI → GHCR images
                                           ↓
        Servers pull + up via `docker compose pull && docker compose up -d`
                                           ↓
        `POST /api/v1/deploy {"confirm": true}` — self-deploy trigger
```

## Images
- `ghcr.io/t-tasin/tars-backend:latest` — Node 1
- `ghcr.io/t-tasin/tars-worker:latest` — Node 2 worker

## Compose Files
- `deploy/node1/docker-compose.yml` — backend, tars-db (postgres:16-alpine), prometheus, grafana, cloudflared (tunnel A)
- `deploy/node2/docker-compose.yml` — redis (7-alpine), qdrant, worker, cloudflared (tunnel B for public dashboard)

llama.cpp runs directly via systemd on Node 2 (NOT in Docker — RAM overhead too high for mmap).

## Deploy Commands

```bash
# Each node
cd /opt/tars/deploy/node{1,2}
docker compose pull
docker compose up -d --remove-orphans

# Self-deploy
POST /api/v1/deploy {"confirm": true}
# Or /deploy via Telegram / iOS
```

## Resource Limits (16GB nodes)

### Node 1 (state + orchestrator)
| Service | RAM cap | CPU |
|---|---|---|
| tars-backend | 2GB | 4 |
| tars-db (postgres:16) | 2GB | — |
| redis (7) | 1.5GB maxmemory | — |
| qdrant | 2GB | 2 |
| prometheus | 0.5GB | — |
| grafana | 0.5GB | — |
| cloudflared (×2) | 0.2GB | — |
| **headroom for OS** | ~5GB | — |

### Node 2 (inference + worker — llama.cpp via systemd, NOT Docker)
| Service | RAM | Threads |
|---|---|---|
| llama-l0 (Qwen3-1.7B) | 1.2GB | 4 threads, optional `-ngl 8` |
| llama-l1 (Qwen3-8B) | 5.0GB | 4 threads + 4 batch, optional `-ngl 12` |
| llama-embed (0.6B) | 0.6GB | 2 threads |
| tars-worker (Docker) | 2GB | 4 |
| code/research sandboxes (Docker burst) | 2GB | 2 |
| **headroom for OS** | ~5GB | — |

## Persistent Volumes
- Node 1: `pgdata`, `grafana-data`, `prom-data`, `tars-data`
- Node 2: `redis-data`, `qdrant-data`, `worker-data`, `/data/models` (host-mounted, GGUFs)

## Servers Have No Dev Tools
Docker + Compose + persistent volumes only. All building in CI/CD. Never SSH in to edit code.

## Rollback
```bash
# Last known good SHA from GH Actions
docker compose down
docker image tag ghcr.io/t-tasin/tars-backend:<sha> ghcr.io/t-tasin/tars-backend:latest
docker compose up -d
```
See `docs/runbook.md` for incident playbooks.
