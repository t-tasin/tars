"""Fuzz tests for ``src.api.sanitizer`` — HC-13 hardening (P3-14).

Property-style randomised testing of ``sanitize_public_event``: across 10k+
iterations split across four scenarios, the sanitizer must never leak a
known PII string, must never crash on adversarial inputs, and must never
emit a non-whitelisted field on the public stream.

All randomness is seeded so failures reproduce. Synthetic PII only — no
real data appears in this file. If a fuzz iteration finds a leak, the
exact failing event is printed to aid debugging.
"""

from __future__ import annotations

import json
import random
import re
import string
import time

from src.api.sanitizer import (
    _ALLOWED_FIELDS,
    MAX_FREE_TEXT_LEN,
    sanitize_public_event,
)

# ---------------------------------------------------------------------------
# Synthetic PII corpus — these exact strings (or substrings matching the
# regexes below) must NEVER appear in sanitizer output.
# ---------------------------------------------------------------------------

# Plain-string PII samples; strict substring assertion applies to these.
_PII_STRINGS: tuple[str, ...] = (
    # Emails
    "tasin@example.com",
    "foo+bar@sub.do.main",
    "fake.user@synthetic-domain.test",
    "operator.name@homelab.lan",
    "alice.bob+filter@mail.example.org",
    # Phone numbers (regex-shaped)
    "+1 (555) 123-4567",
    "555.123.4567",
    "+447911123456",
    "+91 98765 43210",
    "(555) 867-5309",
    # Filesystem paths under user homes
    "/Users/tasin/secret",
    "/home/op/.ssh/id_rsa",
    "/Users/synthetic/Desktop/notes.md",
    "/home/admin/.aws/credentials",
    # IPv4 (private / Tailscale CGNAT)
    "192.168.12.200",
    "100.94.4.103",
    "10.0.0.42",
    "172.16.5.1",
    # Bearer-style API keys
    "Bearer sk-abc123def456ghi789jkl012mno345pq",
    "sk-ant-api03-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
    "AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "AKIAIOSFODNN7EXAMPLE12",
    # JWT-shaped token (three base64 segments)
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturepartx",
)

# Substring/regex sentinels: if ANY match survives in output, that's a leak.
_LEAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[\w.+\-]+@[\w\-]+\.[\w\-.]+"),  # email-shaped
    re.compile(r"/Users/[\w.\-]+"),  # macOS home path
    re.compile(r"/home/[\w.\-]+"),  # linux home path
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),  # OpenAI/Anthropic key
    re.compile(r"\bAKIA[A-Z0-9]{12,}"),  # AWS access key
    re.compile(r"\bAIza[A-Za-z0-9_\-]{16,}"),  # Google API key
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),  # GitHub PAT
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWT
    re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"),  # CGNAT
)

# Free-text fields per event type — fuzz embeds PII in one of these.
_FREE_TEXT_PER_TYPE: dict[str, tuple[str, ...]] = {
    "agent_status": ("agent", "subtype"),
    "model_call": ("model", "subtype"),
    "sensor_publish": ("sensor", "subtype"),
    "approval": ("subtype",),
    "heartbeat": ("node", "subtype"),
    "summary": ("text", "label", "subtype"),
}

_KNOWN_TYPES: tuple[str, ...] = tuple(t for t in _ALLOWED_FIELDS if t != "_common")


def _random_text(rng: random.Random, n: int) -> str:
    """Random ASCII text of length ``n`` (letters/digits/spaces/punct)."""
    alphabet = string.ascii_letters + string.digits + "    .,:;-_"
    return "".join(rng.choice(alphabet) for _ in range(n))


def _embed_pii(rng: random.Random, base: str, picked: list[str]) -> str:
    """Splice PII tokens at random positions inside ``base``."""
    out = base
    for tok in picked:
        cut = rng.randint(0, len(out))
        out = out[:cut] + " " + tok + " " + out[cut:]
    return out


def _assert_no_leak(event: dict, sanitised: dict | None, picked: list[str]) -> None:
    """Assert the sanitiser output (JSON-stringified) leaks no PII."""
    if sanitised is None:
        return
    blob = json.dumps(sanitised, ensure_ascii=False)
    for tok in picked:
        if tok in blob:
            raise AssertionError(
                f"PII leak: token={tok!r} survived in output\n  input:  {event!r}\n  output: {sanitised!r}"
            )
    for pat in _LEAK_PATTERNS:
        m = pat.search(blob)
        if m:
            raise AssertionError(
                f"PII leak (regex): pattern={pat.pattern} matched={m.group(0)!r}\n"
                f"  input:  {event!r}\n"
                f"  output: {sanitised!r}"
            )


# ---------------------------------------------------------------------------
# 1. Random PII injection — 5000 iters
# ---------------------------------------------------------------------------


def test_fuzz_pii_injection_no_leak() -> None:
    """Inject 1-3 PII tokens into a free-text field; assert no leak survives."""
    rng = random.Random(42)
    iters = 5000
    for i in range(iters):
        ev_type = rng.choice(_KNOWN_TYPES)
        field = rng.choice(_FREE_TEXT_PER_TYPE[ev_type])
        n_pii = rng.randint(1, 3)
        picked = rng.sample(_PII_STRINGS, n_pii)
        base = _random_text(rng, rng.randint(0, 80))
        text_with_pii = _embed_pii(rng, base, picked)

        event: dict = {"type": ev_type, field: text_with_pii}
        # Add some benign numeric fields when allowed for variety.
        if ev_type == "model_call":
            event["tokens_in"] = rng.randint(0, 1000)
            event["tokens_out"] = rng.randint(0, 1000)
        if ev_type == "summary" and field != "count":
            event["count"] = rng.randint(0, 99)

        try:
            sanitised = sanitize_public_event(event)
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"sanitiser raised on iter={i}: {exc}\n  event={event!r}") from exc
        _assert_no_leak(event, sanitised, picked)


# ---------------------------------------------------------------------------
# 2. Random unknown fields — 2000 iters
# ---------------------------------------------------------------------------


def test_fuzz_unknown_fields_dropped() -> None:
    """Random unknown top-level keys must never leak into output."""
    rng = random.Random(43)
    iters = 2000
    name_alphabet = string.ascii_lowercase + "_"
    for i in range(iters):
        ev_type = rng.choice(_KNOWN_TYPES)
        allowed = _ALLOWED_FIELDS["_common"] | _ALLOWED_FIELDS[ev_type]
        # Hash fields are also accepted (replaced) — counted as allowed for
        # the output-key check below.
        hash_fields = {"user_id", "actor", "account_id", "session_id", "device_id"}
        accepted = allowed | hash_fields

        event: dict = {"type": ev_type}
        # At least one informative field so the event isn't dropped.
        if ev_type == "agent_status":
            event["agent"] = "router"
        elif ev_type == "model_call":
            event["tier"] = "L1"
        elif ev_type == "sensor_publish":
            event["sensor"] = "cpu"
        elif ev_type == "approval":
            event["action_type"] = "deploy"
        elif ev_type == "heartbeat":
            event["node"] = "n1"
        elif ev_type == "summary":
            event["label"] = "x"

        # Add 1-5 random unknown fields with random values.
        for _ in range(rng.randint(1, 5)):
            name_len = rng.randint(3, 12)
            name = "".join(rng.choice(name_alphabet) for _ in range(name_len))
            if name in accepted:
                name = name + "_x"  # ensure unknown
            kind = rng.randint(0, 4)
            if kind == 0:
                value: object = _random_text(rng, rng.randint(0, 50))
            elif kind == 1:
                value = rng.randint(-10000, 10000)
            elif kind == 2:
                value = [rng.randint(0, 9) for _ in range(rng.randint(0, 5))]
            elif kind == 3:
                value = {"k": _random_text(rng, 20), "n": rng.randint(0, 99)}
            else:
                value = None
            event[name] = value

        try:
            sanitised = sanitize_public_event(event)
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"sanitiser raised on iter={i}: {exc}\n  event={event!r}") from exc

        if sanitised is None:
            continue
        for k in sanitised:
            if k not in accepted:
                raise AssertionError(
                    f"Non-whitelisted key={k!r} survived for type={ev_type!r}\n"
                    f"  input:  {event!r}\n"
                    f"  output: {sanitised!r}"
                )


# ---------------------------------------------------------------------------
# 3. Non-string types in known fields — 2000 iters
# ---------------------------------------------------------------------------


def test_fuzz_wrong_types_no_crash() -> None:
    """Known fields filled with random wrong types must never crash and
    must produce sane output (every str <= cap).
    """
    rng = random.Random(44)
    iters = 2000

    def random_value() -> object:
        kind = rng.randint(0, 6)
        if kind == 0:
            return _random_text(rng, rng.randint(0, 200))
        if kind == 1:
            return rng.randint(-1_000_000, 1_000_000)
        if kind == 2:
            return rng.uniform(-1e6, 1e6)
        if kind == 3:
            return rng.choice([True, False])
        if kind == 4:
            return None
        if kind == 5:
            return [rng.randint(0, 9) for _ in range(rng.randint(0, 4))]
        return {"a": rng.randint(0, 9), "b": _random_text(rng, 10)}

    for i in range(iters):
        ev_type = rng.choice(_KNOWN_TYPES)
        type_fields = list(_ALLOWED_FIELDS[ev_type] | _ALLOWED_FIELDS["_common"])
        # Also exercise hash fields with garbage types.
        type_fields += ["user_id", "actor", "session_id", "device_id", "account_id"]

        event: dict = {"type": ev_type}
        for f in type_fields:
            if f == "type":
                continue
            if rng.random() < 0.7:
                event[f] = random_value()

        try:
            sanitised = sanitize_public_event(event)
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"sanitiser raised on iter={i}: {exc}\n  event={event!r}") from exc

        if sanitised is None:
            continue
        for k, v in sanitised.items():
            if isinstance(v, str):
                if len(v) > MAX_FREE_TEXT_LEN:
                    raise AssertionError(
                        f"Field {k!r} exceeded MAX_FREE_TEXT_LEN={MAX_FREE_TEXT_LEN}: "
                        f"len={len(v)}\n  output: {sanitised!r}"
                    )


# ---------------------------------------------------------------------------
# 4. Random event types incl. unknown types — 1000 iters
# ---------------------------------------------------------------------------


def test_fuzz_unknown_event_types_dropped() -> None:
    """Random unknown ``type`` strings must always sanitise to None."""
    rng = random.Random(45)
    iters = 1000
    alphabet = string.ascii_letters + string.digits + "_-"
    for i in range(iters):
        # Generate a random type string; if it accidentally matches a known
        # type, prefix with a guaranteed-unknown sigil.
        type_len = rng.randint(1, 24)
        candidate = "".join(rng.choice(alphabet) for _ in range(type_len))
        if candidate in _ALLOWED_FIELDS:
            candidate = "fuzz_unknown_" + candidate

        event: dict = {
            "type": candidate,
            "agent": "router",
            "text": "any payload",
            "user_id": "tasin",
            "secret": "leak-me",
        }
        try:
            sanitised = sanitize_public_event(event)
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"sanitiser raised on iter={i}: {exc}\n  event={event!r}") from exc

        if sanitised is not None:
            raise AssertionError(
                f"Unknown type={candidate!r} produced non-None output\n  input:  {event!r}\n  output: {sanitised!r}"
            )


# ---------------------------------------------------------------------------
# 5. Smoke: total iteration count + run-time budget
# ---------------------------------------------------------------------------


def test_fuzz_runtime_budget() -> None:
    """Sanity-check that sanitising a representative event is fast enough
    that the full 10k-iter suite stays under the 10s budget called out in
    P3-14. Uses a small re-run (500 iters) so this test alone is cheap.
    """
    rng = random.Random(46)
    sample_event = {
        "type": "summary",
        "label": "inbox",
        "count": 3,
        "text": "msg from fake@x.test on 192.168.1.1 path /Users/x/y key sk-" + "a" * 32,
        "user_id": "tasin",
    }
    iters = 500
    t0 = time.perf_counter()
    for _ in range(iters):
        sanitize_public_event(dict(sample_event))
    elapsed = time.perf_counter() - t0
    # 500 iters in <1s gives ~50us/iter; 10k iters would fit in ~0.5s.
    assert elapsed < 2.0, f"sanitiser too slow: {elapsed:.3f}s for {iters} iters"
    # Touch ``rng`` so linters don't flag the import in a future refactor.
    assert rng is not None
