# Node 2 systemd units

Three llama.cpp server units + the worker daemon for the muscle node.

## Units

| Unit | Port | Model | GPU | Tier |
|------|------|-------|-----|------|
| `llama-l0` | 8001 | Qwen3-1.7B-Q8_0 (1.8 GB) | CPU-only | L0 reflex |
| `llama-l1` | 8002 | Qwen3-8B-Q4_K_M (4.7 GB) | CPU-only | L1 brain |
| `llama-embed` | 8003 | Qwen3-Embedding-0.6B-Q8_0 (610 MB) | full GPU offload | embedding |
| `tars-worker` | — | — | — | distributed-job consumer (`tars:jobs:queue`) |

## GPU sharing strategy

M620 has **only 1997 MiB VRAM**. It cannot host two models simultaneously and cannot fit any of the generation models with full offload at usable context lengths.

**Decision:** the embedding model wins the GPU. RAG retrieval runs on every query, so embed throughput is the most-impacted bottleneck. Generation models run CPU-only — at 1.7B Q8_0 and 8B Q4_K_M, CPU inference is acceptable on the i7-6700.

If we later want generation on GPU, run only one of `{llama-l0, llama-embed}` at a time, or upgrade to a larger card.

## Install (run from repo root on Node 2)

```bash
sudo cp deploy/node2/systemd/llama-l0.service     /etc/systemd/system/
sudo cp deploy/node2/systemd/llama-l1.service     /etc/systemd/system/
sudo cp deploy/node2/systemd/llama-embed.service  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now llama-l0 llama-l1 llama-embed
```

## Verify

```bash
systemctl status llama-l0 llama-l1 llama-embed --no-pager
ss -tlnp | grep -E ':(8001|8002|8003)'
curl -s http://localhost:8001/v1/models | jq .
curl -s http://localhost:8002/v1/models | jq .
curl -s http://localhost:8003/v1/models | jq .
```

## From Node 1 (over Tailscale)

```bash
curl -s http://100.119.114.125:8001/v1/models | jq .   # L0
curl -s http://100.119.114.125:8002/v1/models | jq .   # L1
curl -s http://100.119.114.125:8003/v1/models | jq .   # embed
```

## Restart (Claude can do via NOPASSWD)

```bash
sudo -n systemctl restart llama-l0
sudo -n systemctl restart llama-l1
sudo -n systemctl restart llama-embed
sudo -n systemctl restart tars-worker
```

These commands are scoped in `/etc/sudoers.d/tars-claude` (P0-18). Boundary holds — `stop`, `disable`, `start` still password-prompt.

---

## tars-worker — distributed-job consumer (P0-17)

The worker daemon polls the Redis queue (`tars:jobs:queue` on Node 1) and runs background jobs (image generation, coding sandbox, etc.) on Node 2.

### One-time install

Repo lives at `/home/tasin/tars` on Node 2. From that path, **as `tasin`**:

```bash
cd /home/tasin/tars/worker
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
```

(Optional) if image jobs need Gemini, write the API key to a worker-local env file:

```bash
echo 'GEMINI_API_KEY=...' > /home/tasin/tars/worker/.env
chmod 600 /home/tasin/tars/worker/.env
```

Install the unit (Tasin — needs sudo password since the unit lives in `/etc`):

```bash
sudo cp /home/tasin/tars/deploy/node2/systemd/tars-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tars-worker
```

### Verify

```bash
systemctl status tars-worker --no-pager
journalctl -u tars-worker -n 50 --no-pager
```

Expected log lines: `worker_starting`, `redis_connected`, `worker_ready`.

### Smoke test (run from Node 1)

Enqueue a no-op job and watch the worker log it:

```bash
docker exec tars-redis redis-cli ZADD tars:jobs:queue 1 \
  '{"id":"smoke-1","task_type":"unknown","payload":{}}'
```

Worker logs `unknown_task_type` for `smoke-1` (expected — confirms pickup). Real task types are dispatched via `JobQueue` from the backend.

### Restart (Claude can do via NOPASSWD)

```bash
sudo -n systemctl restart tars-worker
```

Already scoped in `/etc/sudoers.d/tars-claude` (P0-18 placeholder).

## Tuning notes

- L0 / L1 `--threads 6` leaves cores for the OS + worker daemon. i7-6700 has 4 cores / 8 HT.
- L0 / L1 `--ctx-size 4096` matches Qwen3 base; bump if conversations need longer context (RAM permitting).
- Embed `--ctx-size 512` is hard ceiling — anything bigger OOMs. Chunk inputs at 512 tokens.
- All three: `--no-warmup` skips the dummy forward pass on boot. Trades a little first-request latency for faster systemd `active`.
