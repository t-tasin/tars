# Node 2 systemd units

Three llama.cpp server units for the local inference tier.

## Units

| Unit | Port | Model | GPU | Tier |
|------|------|-------|-----|------|
| `llama-l0` | 8001 | Qwen3-1.7B-Q8_0 (1.8 GB) | CPU-only | L0 reflex |
| `llama-l1` | 8002 | Qwen3-8B-Q4_K_M (4.7 GB) | CPU-only | L1 brain |
| `llama-embed` | 8003 | Qwen3-Embedding-0.6B-Q8_0 (610 MB) | full GPU offload | embedding |

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
```

These commands are scoped in `/etc/sudoers.d/tars-claude` (P0-18). Boundary holds — `stop`, `disable`, `start` still password-prompt.

## Tuning notes

- L0 / L1 `--threads 6` leaves cores for the OS + worker daemon. i7-6700 has 4 cores / 8 HT.
- L0 / L1 `--ctx-size 4096` matches Qwen3 base; bump if conversations need longer context (RAM permitting).
- Embed `--ctx-size 512` is hard ceiling — anything bigger OOMs. Chunk inputs at 512 tokens.
- All three: `--no-warmup` skips the dummy forward pass on boot. Trades a little first-request latency for faster systemd `active`.
