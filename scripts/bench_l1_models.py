#!/usr/bin/env python3
"""
3-way L1 bench: Qwen3-8B vs Gemma4-12B vs Qwen3-30B-A3B-mmap
Measures tok/s, TTFT, total latency, tool-use accuracy, optional Claude quality score.

Usage:
    python scripts/bench_l1_models.py [--models qwen3-8b,gemma4-12b] [--prompts 50] [--out results/]
    ANTHROPIC_API_KEY=sk-... python scripts/bench_l1_models.py --judge

See docs/model_tiers.md §L1 for bench rationale.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Model configs — add Gemma4-12B / 30B-mmap when deployed
# ---------------------------------------------------------------------------

TARS2_HOST = os.environ.get("TARS2_HOST", "100.119.114.125")


@dataclass(frozen=True)
class ModelConfig:
    name: str
    host: str
    port: int
    alias: str
    enabled: bool = True
    note: str = ""


MODELS: list[ModelConfig] = [
    ModelConfig(
        name="qwen3-8b",
        host=TARS2_HOST,
        port=8002,
        alias="qwen3-8b-brain",
        enabled=True,
        note="Live on tars2:8002. CPU-only. P2-05 baseline.",
    ),
    # Uncomment when P2-05e (pull) + deployment done:
    # ModelConfig(
    #     name="gemma4-12b",
    #     host=TARS2_HOST,
    #     port=8004,
    #     alias="gemma4-12b-it",
    #     enabled=False,
    #     note="PLANNED. P2-05e. ~7.5GB Q4_K_M. SOTA tool-use (τ2-bench 86.4%).",
    # ),
    # Uncomment when 30B mmap'd llama-server unit deployed:
    # ModelConfig(
    #     name="qwen3-30b-mmap",
    #     host=TARS2_HOST,
    #     port=8005,
    #     alias="qwen3-30b-a3b",
    #     enabled=False,
    #     note="PLANNED. P2-05a. 18GB mmap'd NVMe. MoE 3B active/token. Est 4-8 tok/s.",
    # ),
]

# ---------------------------------------------------------------------------
# Prompt suite — 50 prompts across real TARS intent categories
# ---------------------------------------------------------------------------

# fmt: off
PROMPTS: list[dict] = [
    # ── briefing / daily summary (10) ──
    {"id": "b01", "category": "briefing", "tool_use": False,
     "text": "Give me a 3-sentence morning briefing for a software engineer. Date: 2026-04-25."},
    {"id": "b02", "category": "briefing", "tool_use": False,
     "text": "Summarize the key things to focus on during a job search week."},
    {"id": "b03", "category": "briefing", "tool_use": False,
     "text": "What are 3 high-leverage habits for a grad student also building a startup?"},
    {"id": "b04", "category": "briefing", "tool_use": False,
     "text": "Draft a 2-sentence status update for a personal AI assistant project checkpoint."},
    {"id": "b05", "category": "briefing", "tool_use": False,
     "text": "List the top 5 metrics a solo developer should track weekly."},
    {"id": "b06", "category": "briefing", "tool_use": False,
     "text": "What's the most important thing to review before a technical interview tomorrow?"},
    {"id": "b07", "category": "briefing", "tool_use": False,
     "text": "Describe in 2 sentences what a productive Saturday looks like for a developer."},
    {"id": "b08", "category": "briefing", "tool_use": False,
     "text": "Give a 1-line priority ranking: emails, pull requests, code review, documentation."},
    {"id": "b09", "category": "briefing", "tool_use": False,
     "text": "Summarize what local-first AI means in one paragraph."},
    {"id": "b10", "category": "briefing", "tool_use": False,
     "text": "List 3 benefits of running AI inference on local hardware vs cloud APIs."},

    # ── finance (8) ──
    {"id": "f01", "category": "finance", "tool_use": False,
     "text": "Explain dollar-cost averaging in 2 sentences for someone new to investing."},
    {"id": "f02", "category": "finance", "tool_use": False,
     "text": "What's the difference between a Roth IRA and a traditional IRA?"},
    {"id": "f03", "category": "finance", "tool_use": False,
     "text": "How should a 25-year-old prioritize: emergency fund, 401k, Roth IRA, or student loans?"},
    {"id": "f04", "category": "finance", "tool_use": False,
     "text": "Give me a simple 50/30/20 budget breakdown for a $4000/month take-home."},
    {"id": "f05", "category": "finance", "tool_use": False,
     "text": "What is expense ratio and why does it matter for index fund investing?"},
    {"id": "f06", "category": "finance", "tool_use": False,
     "text": "Name 3 things to check before switching bank accounts."},
    {"id": "f07", "category": "finance", "tool_use": False,
     "text": "What does 'compound interest' mean and give a simple example."},
    {"id": "f08", "category": "finance", "tool_use": False,
     "text": "How do I calculate my net worth? List the components."},

    # ── health / fitness (7) ──
    {"id": "h01", "category": "health_fitness", "tool_use": False,
     "text": "Design a minimal 20-minute morning workout for someone with no equipment."},
    {"id": "h02", "category": "health_fitness", "tool_use": False,
     "text": "What are 3 evidence-based sleep hygiene tips?"},
    {"id": "h03", "category": "health_fitness", "tool_use": False,
     "text": "How many grams of protein per day for a 75kg person trying to build muscle?"},
    {"id": "h04", "category": "health_fitness", "tool_use": False,
     "text": "Give me a 5-day workout split for intermediate lifters."},
    {"id": "h05", "category": "health_fitness", "tool_use": False,
     "text": "What's the difference between zone 2 cardio and HIIT and when to use each?"},
    {"id": "h06", "category": "health_fitness", "tool_use": False,
     "text": "List 5 micronutrients often deficient in developers who eat at their desk."},
    {"id": "h07", "category": "health_fitness", "tool_use": False,
     "text": "Explain progressive overload in 2 sentences."},

    # ── job search (7) ──
    {"id": "j01", "category": "job_search", "tool_use": False,
     "text": "Write a 2-sentence LinkedIn headline for a backend engineer specializing in AI systems."},
    {"id": "j02", "category": "job_search", "tool_use": False,
     "text": "What should I research before a software engineering interview at a startup?"},
    {"id": "j03", "category": "job_search", "tool_use": False,
     "text": "List 5 questions to ask at the end of a technical interview."},
    {"id": "j04", "category": "job_search", "tool_use": False,
     "text": "How do I negotiate a job offer without damaging the relationship?"},
    {"id": "j05", "category": "job_search", "tool_use": False,
     "text": "What makes a strong GitHub profile for a backend engineer applying to AI companies?"},
    {"id": "j06", "category": "job_search", "tool_use": False,
     "text": "Explain the STAR method for behavioral interview answers."},
    {"id": "j07", "category": "job_search", "tool_use": False,
     "text": "What's the difference between a software engineer and an MLE role?"},

    # ── coding (8) ──
    {"id": "c01", "category": "coding", "tool_use": False,
     "text": "Explain the difference between asyncio and threading in Python in 3 sentences."},
    {"id": "c02", "category": "coding", "tool_use": False,
     "text": "What is a database connection pool and why is it important?"},
    {"id": "c03", "category": "coding", "tool_use": False,
     "text": "Write a Python function to retry an async HTTP call up to 3 times with exponential backoff."},
    {"id": "c04", "category": "coding", "tool_use": False,
     "text": "Explain CAP theorem in 2 sentences for a backend engineer."},
    {"id": "c05", "category": "coding", "tool_use": False,
     "text": "What's the difference between a list and a deque in Python and when to prefer each?"},
    {"id": "c06", "category": "coding", "tool_use": False,
     "text": "Describe 3 common patterns for managing secrets in a containerized application."},
    {"id": "c07", "category": "coding", "tool_use": False,
     "text": "What is a deadlock, how does it occur, and how do you prevent it?"},
    {"id": "c08", "category": "coding", "tool_use": False,
     "text": "Explain Redis sorted sets and give a real-world use case for them."},

    # ── general / daily life (10) ──
    {"id": "g01", "category": "general", "tool_use": False,
     "text": "What are 3 tips for staying focused during deep work sessions?"},
    {"id": "g02", "category": "general", "tool_use": False,
     "text": "Recommend a reading list for someone interested in AI systems and distributed systems."},
    {"id": "g03", "category": "general", "tool_use": False,
     "text": "Explain the Pomodoro technique and when it works best."},
    {"id": "g04", "category": "general", "tool_use": False,
     "text": "What's the best way to handle decision fatigue during a busy work week?"},
    {"id": "g05", "category": "general", "tool_use": False,
     "text": "List 5 free tools useful for solo developers building side projects."},
    {"id": "g06", "category": "general", "tool_use": False,
     "text": "What are the tradeoffs between monolith and microservices for a 1-person team?"},
    {"id": "g07", "category": "general", "tool_use": False,
     "text": "Name 3 ways to reduce context switching when working across multiple projects."},
    {"id": "g08", "category": "general", "tool_use": False,
     "text": "What makes a personal AI assistant genuinely useful vs gimmicky?"},
    {"id": "g09", "category": "general", "tool_use": False,
     "text": "Summarize the difference between RAG and fine-tuning in 3 sentences."},
    {"id": "g10", "category": "general", "tool_use": False,
     "text": "What are 3 signs that a software project is becoming too complex?"},
]

# Tool-use prompts — model must emit valid JSON matching the schema
TOOL_PROMPTS: list[dict] = [
    {"id": "t01", "category": "tool_use", "tool_use": True,
     "schema": {"type": "object", "required": ["action", "priority", "reason"],
                "properties": {"action": {"type": "string"}, "priority": {"type": "string",
                "enum": ["high", "medium", "low"]}, "reason": {"type": "string"}}},
     "text": (
         'You must respond ONLY with valid JSON matching this schema exactly: '
         '{"action": "<string>", "priority": "high|medium|low", "reason": "<string>"}. '
         'No markdown, no explanation. Task: classify this email as an action item. '
         'Email: "Team standup moved to 10am tomorrow. Please update your calendars."'
     )},
    {"id": "t02", "category": "tool_use", "tool_use": True,
     "schema": {"type": "object", "required": ["intent", "complexity"],
                "properties": {"intent": {"type": "string"}, "complexity": {"type": "string",
                "enum": ["low", "medium", "high"]}}},
     "text": (
         'Respond ONLY with valid JSON: {"intent": "<string>", "complexity": "low|medium|high"}. '
         'No other text. Classify: "Can you write me a Python script to scrape job listings from a website?"'
     )},
    {"id": "t03", "category": "tool_use", "tool_use": True,
     "schema": {"type": "object", "required": ["escalate", "signal"],
                "properties": {"escalate": {"type": "boolean"}, "signal": {"type": "string"}}},
     "text": (
         'Respond ONLY with valid JSON: {"escalate": true|false, "signal": "<reason>"}. '
         'Should this be escalated to a senior model? Request: "Generate a photorealistic portrait of me."'
     )},
    {"id": "t04", "category": "tool_use", "tool_use": True,
     "schema": {"type": "object", "required": ["category", "tier", "auto_reply"],
                "properties": {"category": {"type": "string"}, "tier": {"type": "string",
                "enum": ["urgent", "actionable", "informational", "noise"]},
                "auto_reply": {"type": "boolean"}}},
     "text": (
         'Respond ONLY with valid JSON: {"category": "<string>", "tier": "urgent|actionable|informational|noise", '
         '"auto_reply": true|false}. Classify this email: '
         '"Hi, just wanted to say I enjoy your newsletter! Keep up the good work."'
     )},
    {"id": "t05", "category": "tool_use", "tool_use": True,
     "schema": {"type": "object", "required": ["score", "verdict", "reason"],
                "properties": {"score": {"type": "integer", "minimum": 1, "maximum": 10},
                "verdict": {"type": "string"}, "reason": {"type": "string"}}},
     "text": (
         'Respond ONLY with valid JSON: {"score": <1-10>, "verdict": "<string>", "reason": "<string>"}. '
         'Rate the quality of this code response (1=terrible, 10=excellent): '
         '"To reverse a list in Python, use list.reverse() which mutates in place, or reversed() for a lazy iterator."'
     )},
]

ALL_PROMPTS = PROMPTS + TOOL_PROMPTS  # 55 total (50 + 5 tool-use)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class PromptResult:
    model: str
    prompt_id: str
    category: str
    tool_use: bool
    tokens_input: int
    tokens_output: int
    duration_ms: int
    ttft_ms: int | None  # first-token latency (streaming)
    toks_per_sec: float
    finish_reason: str
    tool_valid: bool | None  # None = not a tool prompt
    tool_error: str | None
    error: str | None = None
    judge_score: int | None = None  # 1-10 Claude judge (optional)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT = 120.0


def _chat(
    cfg: ModelConfig,
    prompt: str,
    *,
    system: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.7,
    enable_thinking: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[str, int, int, int, str]:
    """Non-streaming chat. Returns (text, tokens_in, tokens_out, duration_ms, finish_reason)."""
    url = f"http://{cfg.host}:{cfg.port}/v1/chat/completions"
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": cfg.alias,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": enable_thinking},
    }
    t0 = time.monotonic()
    r = httpx.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    duration_ms = int((time.monotonic() - t0) * 1000)
    data = r.json()
    choice = data["choices"][0]
    text = (choice["message"].get("content") or "").strip()
    usage = data.get("usage") or {}
    return (
        text,
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
        duration_ms,
        choice.get("finish_reason", ""),
    )


def _chat_streaming_ttft(
    cfg: ModelConfig,
    prompt: str,
    *,
    max_tokens: int = 512,
    enable_thinking: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[str, int | None]:
    """Streaming chat for TTFT measurement. Returns (full_text, ttft_ms)."""
    url = f"http://{cfg.host}:{cfg.port}/v1/chat/completions"
    payload = {
        "model": cfg.alias,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": max_tokens,
        "stream": True,
        "chat_template_kwargs": {"enable_thinking": enable_thinking},
    }
    t0 = time.monotonic()
    ttft_ms: int | None = None
    chunks: list[str] = []
    with httpx.stream("POST", url, json=payload, timeout=timeout) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            raw = line[6:]
            if raw == "[DONE]":
                break
            try:
                chunk = json.loads(raw)
            except json.JSONDecodeError:
                continue
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content") or ""
            if content and ttft_ms is None:
                ttft_ms = int((time.monotonic() - t0) * 1000)
            chunks.append(content)
    return "".join(chunks).strip(), ttft_ms


# ---------------------------------------------------------------------------
# Tool-use validator
# ---------------------------------------------------------------------------


def _validate_tool_output(text: str, schema: dict) -> tuple[bool, str | None]:
    """Check model emitted valid JSON matching the required keys."""
    try:
        # Strip any accidental markdown fences
        clean = text.strip()
        if clean.startswith("```"):
            clean = "\n".join(clean.split("\n")[1:])
        if clean.endswith("```"):
            clean = "\n".join(clean.split("\n")[:-1])
        data = json.loads(clean.strip())
    except json.JSONDecodeError as e:
        return False, f"json_decode_error: {e}"
    required = schema.get("required", [])
    missing = [k for k in required if k not in data]
    if missing:
        return False, f"missing_keys: {missing}"
    props = schema.get("properties", {})
    for key, prop in props.items():
        if key not in data:
            continue
        if "enum" in prop and data[key] not in prop["enum"]:
            return False, f"{key}={data[key]!r} not in enum {prop['enum']}"
        if prop.get("type") == "integer":
            if not isinstance(data[key], int):
                return False, f"{key} not integer"
            lo = prop.get("minimum")
            hi = prop.get("maximum")
            if lo is not None and data[key] < lo:
                return False, f"{key}={data[key]} < minimum {lo}"
            if hi is not None and data[key] > hi:
                return False, f"{key}={data[key]} > maximum {hi}"
    return True, None


# ---------------------------------------------------------------------------
# Claude judge (optional)
# ---------------------------------------------------------------------------


def _judge_with_claude(prompt: str, response: str) -> int | None:
    """Rate model response quality 1-10 using Claude. Requires ANTHROPIC_API_KEY."""
    try:
        import anthropic  # noqa: PLC0415
    except ImportError:
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    client = anthropic.Anthropic(api_key=api_key)
    judge_prompt = (
        f"Rate this AI response on a scale of 1-10 (1=terrible, 10=excellent). "
        f"Consider: accuracy, completeness, conciseness, and usefulness. "
        f"Respond with ONLY a single integer.\n\n"
        f"PROMPT: {prompt[:300]}\n\n"
        f"RESPONSE: {response[:500]}"
    )
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=10,
        messages=[{"role": "user", "content": judge_prompt}],
    )
    try:
        return int(msg.content[0].text.strip())
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Bench runner
# ---------------------------------------------------------------------------


def bench_model(
    cfg: ModelConfig,
    prompts: list[dict],
    *,
    judge: bool = False,
    verbose: bool = True,
    max_tokens: int = 512,
) -> list[PromptResult]:
    results: list[PromptResult] = []
    total = len(prompts)
    for i, p in enumerate(prompts, 1):
        if verbose:
            print(f"  [{i:2d}/{total}] {p['id']} ({p['category']}) ... ", end="", flush=True)

        is_tool = p.get("tool_use", False)
        enable_thinking = not is_tool  # thinking off for tool-use (deterministic JSON)
        prompt_max_tokens = 128 if is_tool else max_tokens

        try:
            text, tok_in, tok_out, dur_ms, finish = _chat(
                cfg,
                p["text"],
                max_tokens=prompt_max_tokens,
                temperature=0.0 if is_tool else 0.7,
                enable_thinking=enable_thinking,
            )
            tps = round(tok_out / (dur_ms / 1000), 2) if dur_ms > 0 else 0.0

            tool_valid: bool | None = None
            tool_error: str | None = None
            if is_tool:
                tool_valid, tool_error = _validate_tool_output(text, p.get("schema", {}))

            result = PromptResult(
                model=cfg.name,
                prompt_id=p["id"],
                category=p["category"],
                tool_use=is_tool,
                tokens_input=tok_in,
                tokens_output=tok_out,
                duration_ms=dur_ms,
                ttft_ms=None,
                toks_per_sec=tps,
                finish_reason=finish,
                tool_valid=tool_valid,
                tool_error=tool_error,
                judge_score=_judge_with_claude(p["text"], text) if judge and not is_tool else None,
            )
            if verbose:
                tool_tag = f" tool={'✓' if tool_valid else '✗'}" if is_tool else ""
                print(f"{dur_ms}ms {tps:.1f} tok/s{tool_tag}")

        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"ERROR: {e}")
            result = PromptResult(
                model=cfg.name,
                prompt_id=p["id"],
                category=p["category"],
                tool_use=is_tool,
                tokens_input=0,
                tokens_output=0,
                duration_ms=0,
                ttft_ms=None,
                toks_per_sec=0.0,
                finish_reason="error",
                tool_valid=None,
                tool_error=None,
                error=str(e),
            )

        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Summary + CSV output
# ---------------------------------------------------------------------------


def _summary(results: list[PromptResult]) -> dict:
    ok = [r for r in results if not r.error]
    if not ok:
        return {"error": "all prompts failed"}
    tps_vals = [r.toks_per_sec for r in ok if not r.tool_use]
    tool_results = [r for r in ok if r.tool_use]
    tool_pass = sum(1 for r in tool_results if r.tool_valid)
    judge_vals = [r.judge_score for r in ok if r.judge_score is not None]
    return {
        "model": results[0].model,
        "prompts_total": len(results),
        "prompts_ok": len(ok),
        "prompts_error": len(results) - len(ok),
        "avg_toks_per_sec": round(sum(tps_vals) / len(tps_vals), 2) if tps_vals else None,
        "min_toks_per_sec": round(min(tps_vals), 2) if tps_vals else None,
        "max_toks_per_sec": round(max(tps_vals), 2) if tps_vals else None,
        "avg_latency_ms": round(sum(r.duration_ms for r in ok) / len(ok)),
        "tool_use_total": len(tool_results),
        "tool_use_pass": tool_pass,
        "tool_use_accuracy": round(tool_pass / len(tool_results), 3) if tool_results else None,
        "avg_judge_score": round(sum(judge_vals) / len(judge_vals), 2) if judge_vals else None,
    }


def _write_csv(results: list[PromptResult], path: Path) -> None:
    rows = [asdict(r) for r in results]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _print_summary_table(summaries: list[dict]) -> None:
    cols = ["model", "avg_toks_per_sec", "min_toks_per_sec", "max_toks_per_sec",
            "avg_latency_ms", "tool_use_accuracy", "avg_judge_score",
            "prompts_ok", "prompts_error"]
    header = " | ".join(f"{c:<22}" for c in cols)
    print("\n" + "=" * len(header))
    print("L1 BENCH RESULTS")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for s in summaries:
        row = " | ".join(f"{str(s.get(c, 'n/a')):<22}" for c in cols)
        print(row)
    print("=" * len(header))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="TARS L1 model bench harness")
    parser.add_argument(
        "--models",
        default="",
        help="Comma-separated model names to run (default: all enabled). E.g. qwen3-8b",
    )
    parser.add_argument(
        "--prompts",
        type=int,
        default=len(ALL_PROMPTS),
        help=f"Number of prompts to run (default: {len(ALL_PROMPTS)} = all)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=512,
        dest="max_tokens",
        help="Max output tokens per prompt (default: 512; use 128 for faster throughput measurement)",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Enable Claude Haiku quality judge (requires ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--out",
        default="scripts/bench_results",
        help="Output directory for CSV + JSON results",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=True,
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    if args.quiet:
        args.verbose = False

    # Select models
    selected_names = {n.strip() for n in args.models.split(",") if n.strip()}
    active_models = [
        m for m in MODELS
        if m.enabled and (not selected_names or m.name in selected_names)
    ]
    if not active_models:
        print("No enabled models to run. Check --models or MODELS config.")
        sys.exit(1)

    # Select prompts
    prompts = ALL_PROMPTS[: args.prompts]

    # Output dir
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out_dir = Path(args.out) / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    all_results: list[PromptResult] = []
    summaries: list[dict] = []

    for cfg in active_models:
        print(f"\n{'─' * 60}")
        print(f"Benching: {cfg.name}  ({cfg.note})")
        print(f"Endpoint: http://{cfg.host}:{cfg.port}  alias={cfg.alias}")
        print(f"Prompts:  {len(prompts)}")
        print("─" * 60)

        # Connectivity check
        try:
            r = httpx.get(f"http://{cfg.host}:{cfg.port}/v1/models", timeout=5)
            r.raise_for_status()
        except Exception as e:
            print(f"SKIP: {cfg.name} unreachable — {e}")
            continue

        results = bench_model(cfg, prompts, judge=args.judge, verbose=args.verbose, max_tokens=args.max_tokens)
        all_results.extend(results)

        summary = _summary(results)
        summaries.append(summary)

        # Per-model CSV
        csv_path = out_dir / f"{cfg.name}.csv"
        _write_csv(results, csv_path)
        print(f"\nSaved: {csv_path}")

    if not summaries:
        print("No results. All models unreachable.")
        sys.exit(1)

    # Combined JSON summary
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summaries, indent=2))
    print(f"Saved: {summary_path}")

    _print_summary_table(summaries)

    # Recommendation
    if len(summaries) > 1:
        best_tps = max(summaries, key=lambda s: s.get("avg_toks_per_sec") or 0)
        best_tool = max(summaries, key=lambda s: s.get("tool_use_accuracy") or 0)
        print(f"\nFastest tok/s: {best_tps['model']} ({best_tps['avg_toks_per_sec']} tok/s)")
        print(f"Best tool-use: {best_tool['model']} ({best_tool['tool_use_accuracy']:.1%})")
        if args.judge:
            best_q = max(summaries, key=lambda s: s.get("avg_judge_score") or 0)
            print(f"Best quality:  {best_q['model']} (score {best_q['avg_judge_score']}/10)")
        print("\nReview summary.json to pick L1 winner. Update docs/model_tiers.md + FEATURES.md.")


if __name__ == "__main__":
    main()
