#!/usr/bin/env python3
"""TARS integration smoke test.

Probes every external API key + local LLM endpoint + backend /health.
Reports OK/FAIL per integration. NEVER prints secret values.

Usage (on tars1):
    /opt/tars/backend/.venv/bin/python3 scripts/smoke_all.py
    /opt/tars/backend/.venv/bin/python3 scripts/smoke_all.py --json
    /opt/tars/backend/.venv/bin/python3 scripts/smoke_all.py --only gemini,telegram

Exit code is the count of failed checks (0 = all green).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Env loading — read /opt/tars/deploy/node1/.env if present, never print values.
# ---------------------------------------------------------------------------

_ENV_PATHS = [
    Path("/opt/tars/deploy/node1/.env"),
    Path(__file__).resolve().parent.parent / ".env",
]


def _load_env() -> None:
    for p in _ENV_PATHS:
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())
        return


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class Probe:
    name: str
    ok: bool
    detail: str


def has(*keys: str) -> bool:
    return all(os.environ.get(k) for k in keys)


# ---------------------------------------------------------------------------
# Individual probes
# ---------------------------------------------------------------------------


async def probe_gemini() -> Probe:
    if not has("GEMINI_API_KEY"):
        return Probe("Gemini", False, "no key")
    key = os.environ["GEMINI_API_KEY"]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}")
        if r.status_code == 200:
            return Probe("Gemini", True, f"{len(r.json().get('models', []))} models")
        return Probe("Gemini", False, f"HTTP {r.status_code}")
    except Exception as e:
        return Probe("Gemini", False, type(e).__name__)


async def probe_openweather() -> Probe:
    if not has("OPENWEATHERMAP_API_KEY"):
        return Probe("OpenWeather", False, "no key")
    key = os.environ["OPENWEATHERMAP_API_KEY"]
    lat = os.environ.get("WEATHER_DEFAULT_LAT", "40.8")
    lon = os.environ.get("WEATHER_DEFAULT_LON", "-81.9")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={"lat": lat, "lon": lon, "appid": key, "units": "imperial"},
            )
        if r.status_code == 200:
            d = r.json()
            return Probe("OpenWeather", True, f"{d.get('name','?')} {d['main']['temp']}F")
        return Probe("OpenWeather", False, f"HTTP {r.status_code}")
    except Exception as e:
        return Probe("OpenWeather", False, type(e).__name__)


async def probe_telegram() -> Probe:
    if not has("TELEGRAM_BOT_TOKEN"):
        return Probe("Telegram", False, "no token")
    tok = os.environ["TELEGRAM_BOT_TOKEN"]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.telegram.org/bot{tok}/getMe")
        if r.status_code == 200 and r.json().get("ok"):
            return Probe("Telegram", True, f"@{r.json()['result'].get('username','?')}")
        return Probe("Telegram", False, f"HTTP {r.status_code}")
    except Exception as e:
        return Probe("Telegram", False, type(e).__name__)


async def probe_github() -> Probe:
    if not has("GITHUB_PAT"):
        return Probe("GitHub", False, "no PAT")
    tok = os.environ["GITHUB_PAT"]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
            )
        if r.status_code == 200:
            return Probe("GitHub", True, f"user={r.json().get('login','?')}")
        return Probe("GitHub", False, f"HTTP {r.status_code}")
    except Exception as e:
        return Probe("GitHub", False, type(e).__name__)


async def probe_notion() -> Probe:
    if not has("NOTION_TOKEN"):
        return Probe("Notion", False, "no token")
    tok = os.environ["NOTION_TOKEN"]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://api.notion.com/v1/users/me",
                headers={"Authorization": f"Bearer {tok}", "Notion-Version": "2022-06-28"},
            )
        if r.status_code == 200:
            return Probe("Notion", True, f"user={r.json().get('name','?')}")
        return Probe("Notion", False, f"HTTP {r.status_code}")
    except Exception as e:
        return Probe("Notion", False, type(e).__name__)


async def probe_brave() -> Probe:
    if not has("BRAVE_API_KEY"):
        return Probe("Brave", False, "no key")
    key = os.environ["BRAVE_API_KEY"]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": "test"},
                headers={"X-Subscription-Token": key, "Accept": "application/json"},
            )
        return Probe("Brave", r.status_code == 200, f"HTTP {r.status_code}")
    except Exception as e:
        return Probe("Brave", False, type(e).__name__)


async def probe_serpapi() -> Probe:
    if not has("SERPAPI_KEY"):
        return Probe("SerpAPI", False, "no key")
    key = os.environ["SERPAPI_KEY"]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://serpapi.com/account", params={"api_key": key})
        if r.status_code == 200:
            d = r.json()
            return Probe("SerpAPI", True, f"plan={d.get('plan_name','?')} left={d.get('searches_left','?')}")
        return Probe("SerpAPI", False, f"HTTP {r.status_code}")
    except Exception as e:
        return Probe("SerpAPI", False, type(e).__name__)


async def probe_picovoice() -> Probe:
    return Probe(
        "Picovoice",
        bool(os.environ.get("PICOVOICE_ACCESS_KEY")),
        "key present (no public probe)" if os.environ.get("PICOVOICE_ACCESS_KEY") else "no key",
    )


async def probe_caldav() -> Probe:
    if not has("ICLOUD_CALDAV_USER", "ICLOUD_CALDAV_PASSWORD"):
        return Probe("CalDAV", False, "no creds")
    user = os.environ["ICLOUD_CALDAV_USER"]
    pw = os.environ["ICLOUD_CALDAV_PASSWORD"]
    try:
        async with httpx.AsyncClient(timeout=10, auth=(user, pw)) as c:
            r = await c.request("PROPFIND", "https://caldav.icloud.com/", headers={"Depth": "0"})
        ok = r.status_code in (200, 207, 301, 302)
        return Probe("CalDAV", ok, f"HTTP {r.status_code}")
    except Exception as e:
        return Probe("CalDAV", False, type(e).__name__)


async def _gmail_probe(label: str, cred_env: str, refresh_env: str) -> Probe:
    if not has(cred_env, refresh_env):
        return Probe(label, False, "no creds/refresh")
    try:
        creds_obj = json.loads(base64.b64decode(os.environ[cred_env]))
        inner = creds_obj.get("installed") or creds_obj.get("web") or creds_obj
        cid = inner["client_id"]
        csec = inner["client_secret"]
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": cid,
                    "client_secret": csec,
                    "refresh_token": os.environ[refresh_env],
                    "grant_type": "refresh_token",
                },
            )
        if r.status_code != 200 or not r.json().get("access_token"):
            return Probe(label, False, f"refresh HTTP {r.status_code}")
        tok = r.json()["access_token"]
        async with httpx.AsyncClient(timeout=10) as c:
            p = await c.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                headers={"Authorization": f"Bearer {tok}"},
            )
        if p.status_code != 200:
            return Probe(label, False, f"profile HTTP {p.status_code}")
        em = p.json().get("emailAddress", "?")
        masked = em[:3] + "***@" + em.split("@")[-1] if "@" in em else "***"
        return Probe(label, True, masked)
    except Exception as e:
        return Probe(label, False, type(e).__name__)


async def probe_gmail_personal() -> Probe:
    return await _gmail_probe("Gmail-Personal", "GMAIL_PERSONAL_CREDENTIALS", "GMAIL_PERSONAL_REFRESH_TOKEN")


async def probe_gmail_pro() -> Probe:
    return await _gmail_probe("Gmail-Pro", "GMAIL_PROFESSIONAL_CREDENTIALS", "GMAIL_PROFESSIONAL_REFRESH_TOKEN")


async def probe_plaid() -> Probe:
    if not has("PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ACCESS_TOKEN"):
        return Probe("Plaid", False, "no creds")
    env = os.environ.get("PLAID_ENV", "sandbox")
    base = {
        "sandbox": "https://sandbox.plaid.com",
        "development": "https://development.plaid.com",
        "production": "https://production.plaid.com",
    }.get(env, "https://sandbox.plaid.com")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{base}/accounts/get",
                json={
                    "client_id": os.environ["PLAID_CLIENT_ID"],
                    "secret": os.environ["PLAID_SECRET"],
                    "access_token": os.environ["PLAID_ACCESS_TOKEN"],
                },
            )
        if r.status_code == 200:
            return Probe("Plaid", True, f"env={env} accounts={len(r.json().get('accounts', []))}")
        return Probe("Plaid", False, f"HTTP {r.status_code}")
    except Exception as e:
        return Probe("Plaid", False, type(e).__name__)


async def probe_grafana() -> Probe:
    if not has("GRAFANA_URL", "GRAFANA_API_KEY"):
        return Probe("Grafana", False, "no creds")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{os.environ['GRAFANA_URL'].rstrip('/')}/api/datasources",
                headers={"Authorization": f"Bearer {os.environ['GRAFANA_API_KEY']}"},
            )
        if r.status_code == 200:
            return Probe("Grafana", True, f"datasources={len(r.json())}")
        return Probe("Grafana", False, f"HTTP {r.status_code}")
    except Exception as e:
        return Probe("Grafana", False, type(e).__name__)


# Local LLMs — Tailscale IP of tars2 (matches local_client.py default).
_QWEN_HOST = os.environ.get("TARS_LLM_HOST", "100.119.114.125")


async def _llm_probe(label: str, port: int) -> Probe:
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"http://{_QWEN_HOST}:{port}/v1/models")
        if r.status_code == 200:
            return Probe(label, True, f"loaded={len(r.json().get('data', []))}")
        return Probe(label, False, f"HTTP {r.status_code}")
    except Exception as e:
        return Probe(label, False, type(e).__name__)


async def probe_qwen_reflex() -> Probe:
    return await _llm_probe("Qwen3-REFLEX-1.7B", 8001)


async def probe_qwen_brain() -> Probe:
    return await _llm_probe("Qwen3-BRAIN-8B", 8002)


async def probe_qwen_embed() -> Probe:
    return await _llm_probe("Qwen3-EMBED", 8003)


async def probe_qwen_brain_inference() -> Probe:
    """Tiny chat completion against Qwen3-8B to confirm generation actually works
    (catches thinking-mode + empty-content bugs)."""
    payload = {
        "model": "qwen3-8b-brain",
        "messages": [{"role": "user", "content": "Say 'pong' and nothing else."}],
        "max_tokens": 20,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"http://{_QWEN_HOST}:8002/v1/chat/completions", json=payload)
        if r.status_code != 200:
            return Probe("Qwen3-BRAIN-infer", False, f"HTTP {r.status_code}")
        d = r.json()
        content = (d["choices"][0]["message"].get("content") or "").strip()
        toks = d.get("usage", {}).get("completion_tokens", 0)
        ms = d.get("timings", {}).get("predicted_ms", 0)
        tok_s = (toks / (ms / 1000)) if ms else 0
        if not content:
            return Probe("Qwen3-BRAIN-infer", False, f"empty content (finish={d['choices'][0].get('finish_reason')})")
        return Probe("Qwen3-BRAIN-infer", True, f"{toks}tok @ {tok_s:.1f}tok/s")
    except Exception as e:
        return Probe("Qwen3-BRAIN-infer", False, type(e).__name__)


async def probe_backend_health() -> Probe:
    url = os.environ.get("TARS_BACKEND_HEALTH_URL", "http://localhost:8000/api/v1/health")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url)
        if r.status_code == 200:
            return Probe("Backend /health", True, str(r.json().get("status", "?")))
        return Probe("Backend /health", False, f"HTTP {r.status_code}")
    except Exception as e:
        return Probe("Backend /health", False, type(e).__name__)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PROBES = {
    "gemini": probe_gemini,
    "openweather": probe_openweather,
    "telegram": probe_telegram,
    "github": probe_github,
    "notion": probe_notion,
    "brave": probe_brave,
    "serpapi": probe_serpapi,
    "picovoice": probe_picovoice,
    "caldav": probe_caldav,
    "gmail_personal": probe_gmail_personal,
    "gmail_pro": probe_gmail_pro,
    "plaid": probe_plaid,
    "grafana": probe_grafana,
    "qwen_reflex": probe_qwen_reflex,
    "qwen_brain": probe_qwen_brain,
    "qwen_embed": probe_qwen_embed,
    "qwen_brain_inference": probe_qwen_brain_inference,
    "backend_health": probe_backend_health,
}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated probe names to run")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of table")
    args = ap.parse_args()

    _load_env()

    selected = (
        [PROBES[k] for k in args.only.split(",") if k in PROBES] if args.only else list(PROBES.values())
    )
    results: list[Probe] = list(await asyncio.gather(*(p() for p in selected)))

    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=2))
    else:
        print()
        print(f"{'INTEGRATION':<24} {'STATUS':<6}  DETAIL")
        print("-" * 70)
        for r in results:
            print(f"{r.name:<24} {'OK' if r.ok else 'FAIL':<6}  {r.detail}")
        fails = sum(1 for r in results if not r.ok)
        print()
        print(f"Total: {len(results)}  OK: {len(results) - fails}  FAIL: {fails}")

    return sum(1 for r in results if not r.ok)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
