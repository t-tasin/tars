# Claude Self-Improvement Log

> After ANY correction from Tasin → add entry here.
> Review at session start (SessionStart hook surfaces automatically).
> Iterate until mistake rate drops.

## Format

```
## YYYY-MM-DD — <short tag>

**Correction:** <what Tasin said>
**What I did wrong:** <root cause>
**Rule for future:** <what to do instead>
**Applies to:** <when this rule kicks in>
```

---

## 2026-04-22 — verify hardware before spec'ing models

**Correction:** Initial model recommendations assumed i7-7700T + 32GB RAM. Probe revealed i7-6700 + 16GB + Quadro M620 GPU.
**What I did wrong:** Took user's mention of "HP Z2 Mini G3" as ground truth for specific CPU/RAM SKU. That chassis ships multiple configs. My Qwen3.6-35B-A3B pick (20GB RAM) would not have fit.
**Rule for future:** Before spec'ing anything that depends on hardware limits (RAM, VRAM, thread count, AVX flags, GPU arch): run or ask for the probe script output. Never assume a hardware config from a product line name.
**Applies to:** Model selection, llama.cpp build flags, Docker resource caps, any memory-bound design choice.

---

## 2026-04-22 — CUDA toolkit needs gcc pin on Ubuntu 24.04

**Correction:** Smoke compile errored: `gcc versions later than 12 are not supported` on CUDA 12.2.
**What I did wrong:** Didn't flag the gcc-13 default on Ubuntu 24.04 when recommending CUDA install. Would have wasted a round-trip.
**Rule for future:** When installing CUDA toolkit on Ubuntu 24.04+: always include `sudo apt install -y gcc-12 g++-12` and set `CUDAHOSTCXX=/usr/bin/g++-12` in shell profile, upfront.
**Applies to:** Any CUDA build on Ubuntu 24.04 w/ CUDA ≤12.5 (gcc-12 cap). Also applies to llama.cpp/whisper.cpp build flags.

---

## 2026-04-22 — Reddit data point ≠ lock model choice

**Correction:** Tasin flagged Reddit claim that Gemma 4 runs on 5GB RAM. I had defaulted to Qwen3 without benching Gemma 4.
**What I did wrong:** Treated my prior recommendation as locked rather than a starting hypothesis. Didn't invite empirical test.
**Rule for future:** Model picks are always hypotheses until benched on the real hardware with real workload. Phase 2 should have a 3-way (minimum 2-way) bench row in FEATURES.md, not a single predetermined winner.
**Applies to:** Any time a model is being selected for L0/L1 local tier, any time quality + speed tradeoff matters.
