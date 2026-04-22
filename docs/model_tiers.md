# Model Tiers + Escalation Signals

## Tiers

Locked to 16GB RAM per node (verified 2026-04-21 via tars-probe).

| Tier | Model | Location | Role | Cost |
|------|-------|----------|------|------|
| L0 Reflex | Qwen3-1.7B-tars (LoRA persona) Q4_K_M, 1.2GB | Node 2, port 8001 | Intent aug, quick formatting, voice responses, first-token replies | $0 |
| L1 Brain | Qwen3-8B-Instruct-2507 Q4_K_M, 5.0GB | Node 2, port 8002 | Briefings, drafts, wiki Q&A, routine coding, finance summaries | $0 |
| L1 Embed | Qwen3-Embedding-0.6B, 0.6GB | Node 2, port 8003 | Wiki, wardrobe, email retrieval | $0 |
| L2 Web | Gemini 2.5 Flash (with search grounding) | Cloud | Web-grounded research, image understanding, OCR | ~$0.1/1M in, $0.4/1M out |
| L3 Deep | Gemini 2.5 Pro | Cloud | Long-context research, complex synthesis | ~$1.25/1M in, $5/1M out |
| L3 Image | Gemini 2.5 Flash Image ("nano banana") | Cloud | Image generation/editing | Per-image |
| L4 Reasoning | Claude Sonnet | Cloud | Critical diagnostics, serious discussions, architectural code | Max plan |
| L5 Escalation | Claude Opus | Cloud | Tier-3 approvals (email prof, push prod, infra change) | Max plan |

### GPU Use
Quadro M620 (2GB VRAM) too small for L0/L1 full-model offload. Best uses:
1. **Whisper-small.en CUDA** for STT (~500MB VRAM) — frees CPU for llama
2. Optional `-ngl 8-12` partial layer offload to L0 or L1 (modest speedup)
3. Embedding batch encoding on GPU (small batches, marginal gain)

## Routing Rule

Default → L0 or L1 local.
Escalate to cloud **only** when an `EscalationSignal` fires.

```python
class EscalationSignal(Enum):
    WEB_GROUNDING_NEEDED   = "web_grounding"
    DEEP_RESEARCH          = "deep_research"
    IMAGE_GENERATION       = "image_gen"
    IMAGE_UNDERSTANDING    = "image_in"
    OCR_DOCUMENT           = "ocr"
    LONG_CONTEXT_REQUIRED  = "long_ctx"        # >128k tokens
    CRITICAL_DIAGNOSTIC    = "crit_diag"       # AtlasDesk crash etc
    SERIOUS_DISCUSSION     = "serious"         # user-flagged
    ARCHITECTURAL_CODE     = "arch_code"       # >100 LOC, multi-file
    TIER3_ESCALATION       = "tier3"
    LOCAL_LOW_CONFIDENCE   = "uncertain"       # L1 self-flags
```

## Route() Logic

```python
def route(intent: Intent, signals: set[EscalationSignal]) -> ModelRoute:
    if IMAGE_GENERATION in signals:       return L3_IMAGE
    if IMAGE_UNDERSTANDING in signals:    return L2_WEB (vision mode)
    if OCR_DOCUMENT in signals:           return L2_WEB (vision mode)
    if TIER3_ESCALATION in signals:       return L5_OPUS
    if CRITICAL_DIAGNOSTIC in signals:    return L4_SONNET
    if SERIOUS_DISCUSSION in signals:     return L4_SONNET
    if ARCHITECTURAL_CODE in signals:     return L4_SONNET (mcp: coding)
    if DEEP_RESEARCH in signals:          return L3_PRO
    if LONG_CONTEXT_REQUIRED in signals:  return L3_PRO
    if WEB_GROUNDING_NEEDED in signals:   return L2_WEB (tools: google_search)
    if intent.complexity == "reflex":     return L0_REFLEX
    return L1_BRAIN
```

## Fallback Chain (HC-09)

1. Primary model → fail
2. Same-family fallback (local → local, cloud → cloud)
3. Cross-family fallback (Claude ↔ Gemini)
4. Final: raw data delivery with explanation

## Self-Escalation JSON Protocol

L1 system prompt includes:

```
You are T.A.R.S.'s local brain. If the question requires:
- current web information you don't have → reply:
  {"escalate": "web", "reason": "..."}
- complex multi-step reasoning you're unsure about → reply:
  {"escalate": "claude", "reason": "..."}
- deep research across many sources → reply:
  {"escalate": "gemini_pro", "reason": "..."}
Otherwise answer directly. Never fabricate. Escalate when uncertain.
```

Engine parses JSON. Re-routes. Saves cost vs blanket-escalation.

Target self-escalation rate: <20%.

## Target Distribution (Steady State)

- L0 + L1 local: **85%**
- L2 Gemini Flash: ~10%
- L3 Gemini Pro/Image: ~3%
- L4 Claude Sonnet: ~1.5%
- L5 Claude Opus: ~0.5%

## Budget Enforcement (HC-12)

`model_usage` table tracks every call. Alert at 70% of Claude daily/weekly limit. Monthly AI cost target <$15. Track power draw separately.

## Model Choice Rationale

**L1 is a 3-way bench (P2-05d), not a locked choice.** Candidates:
1. **Qwen3-8B-Instruct-2507 Q4_K_M** — fastest tok/s, coder variant available, same family as L0/Embed
2. **Gemma 4 12B-it Q4_K_M** — SOTA tool use (τ2-bench 86.4%), multimodal, Apache 2.0, slower tok/s
3. **Qwen3-30B-A3B Q4_K_M mmap'd** — highest ceiling but NVMe-paged experts, unknown sustained rate

Bench on Node 2 w/ 50 real TARS prompts. Measure tok/s, first-token, quality (Claude judge), tool-use accuracy. Promote winner empirically.

Baseline hypothesis (why Qwen3-8B first):
- Hardware ceiling: 16GB total RAM. After OS/worker/sandboxes (~6GB), inference budget ~10GB
- L0 (1.2GB) + L1 (5GB) + Embed (0.6GB) = 6.8GB, headroom safe
- Qwen3-30B-A3B Q4 (18GB) DOES NOT FIT — requires mmap-from-disk = slow first token
- Qwen3-14B Q4 (8.5GB) fits but dense = ~3-5 tok/s CPU = sluggish
- Qwen3-8B = 8-12 tok/s on i7-6700 CPU, conversational feel
- Quality: SOTA at 8B class, agentic tool-use trained, multilingual
- Coder-variant available (`Qwen3-Coder-8B`) if coding workload heavy enough

**Why Qwen3-1.7B as L0 base:**
- 40-60 tok/s CPU Q4_K_M = instant feel
- 1.2GB resident — fits anywhere
- LoRA-fine-tunable on persona corpus via Unsloth (single 8GB GPU or Mac MLX)
- Strong instruction-following at this size

**Why Qwen3-Embedding-0.6B:**
- MTEB-competitive at tiny footprint
- Same family = consistent tokenizer + easier debugging
- Alt: Gemini text-embedding-004 (cloud, free tier) if local CPU overloaded

**Stretch experiment (Phase 2 spike — bench, do not promote without data):**
Qwen3-30B-A3B Q4_K_M mmap'd from NVMe (18GB > 16GB RAM). MoE activates 3B/token, so only hot experts pinned. NVMe Samsung MZVLW256 reads ~3GB/s. Realistic est: 4-8 tok/s sustained, slow first token. If bench passes user-acceptance, swap into L1 slot.

**Reverify after benchmark:** llama-bench on Node 2 with both candidates, same prompt, same seed. Pick the winner empirically not theoretically.
