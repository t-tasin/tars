"""Client for Google Gemini Flash, Pro, and Vision models."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import structlog
from google import genai
from google.genai import types

log = structlog.get_logger()

# Retry config for rate-limit (429) and transient errors.
_MAX_RETRIES = 3
_BACKOFF_SECONDS = (1, 2, 4)
_DEFAULT_TIMEOUT_S = 30


@dataclass(frozen=True)
class GeminiResponse:
    """Structured response from a Gemini API call."""

    text: str
    model: str
    tokens_input: int
    tokens_output: int
    duration_ms: int
    finish_reason: str


class GeminiClient:
    """Client for Google Gemini Flash, Pro, and Vision models.

    Uses the unified ``google-genai`` SDK with ``client.aio`` for async calls.
    All calls are retried on 429 (rate-limit) with exponential backoff.
    """

    def __init__(self, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        model: str = "gemini-2.0-flash",
        system_instruction: str | None = None,
        temperature: float = 0.7,
        max_output_tokens: int = 2048,
        response_format: str | None = None,
    ) -> GeminiResponse:
        """Generate text from Gemini.

        Args:
            prompt: User prompt / content.
            model: Model name (e.g. ``gemini-2.0-flash``, ``gemini-2.0-pro``).
            system_instruction: Optional system-level instruction.
            temperature: Sampling temperature.
            max_output_tokens: Max tokens in the response.
            response_format: Set to ``"json"`` for JSON output.
        """
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        if response_format == "json":
            config.response_mime_type = "application/json"

        return await self._call(model=model, contents=prompt, config=config)

    async def generate_with_vision(
        self,
        prompt: str,
        images: list[bytes],
        model: str = "gemini-2.0-flash",
        system_instruction: str | None = None,
    ) -> GeminiResponse:
        """Generate text with image input.

        Args:
            prompt: Text prompt describing the task.
            images: List of raw image bytes (JPEG/PNG).
            model: Model name with vision support.
            system_instruction: Optional system-level instruction.
        """
        parts: list[types.Part | str] = [prompt]
        for img in images:
            parts.append(types.Part.from_bytes(data=img, mime_type="image/jpeg"))

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
        )

        return await self._call(model=model, contents=parts, config=config)

    async def classify(
        self,
        text: str,
        categories: list[str],
        system_instruction: str | None = None,
    ) -> str:
        """Quick classification helper — returns one of the given categories.

        Asks the model to respond with exactly one category name.
        """
        cats = ", ".join(categories)
        prompt = (
            f"Classify the following text into exactly one of these categories: {cats}\n\n"
            f"Text: {text}\n\n"
            f"Respond with ONLY the category name, nothing else."
        )
        response = await self.generate(
            prompt=prompt,
            model="gemini-2.0-flash",
            system_instruction=system_instruction,
            temperature=0.0,
            max_output_tokens=64,
        )
        result = response.text.strip()
        # Normalize: pick the first matching category (case-insensitive)
        lower = result.lower()
        for cat in categories:
            if cat.lower() == lower:
                return cat
        # Fallback: return raw result even if it doesn't match exactly
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _call(
        self,
        model: str,
        contents: str | list[types.Part | str],
        config: types.GenerateContentConfig,
    ) -> GeminiResponse:
        """Execute a generate_content call with retry and metrics."""
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            start = time.monotonic()
            try:
                response = await asyncio.wait_for(
                    self._client.aio.models.generate_content(
                        model=model,
                        contents=contents,
                        config=config,
                    ),
                    timeout=_DEFAULT_TIMEOUT_S,
                )
                duration_ms = int((time.monotonic() - start) * 1000)

                usage = response.usage_metadata
                tokens_in = usage.prompt_token_count or 0 if usage else 0
                tokens_out = usage.candidates_token_count or 0 if usage else 0

                finish = ""
                if response.candidates and response.candidates[0].finish_reason:
                    finish = str(response.candidates[0].finish_reason)

                text = response.text or ""

                log.info(
                    "gemini_call",
                    model=model,
                    tokens_input=tokens_in,
                    tokens_output=tokens_out,
                    duration_ms=duration_ms,
                    finish_reason=finish,
                )

                return GeminiResponse(
                    text=text,
                    model=model,
                    tokens_input=tokens_in,
                    tokens_output=tokens_out,
                    duration_ms=duration_ms,
                    finish_reason=finish,
                )

            except Exception as exc:
                duration_ms = int((time.monotonic() - start) * 1000)
                last_exc = exc

                # Retry on rate-limit (429) or transient server errors
                is_retryable = _is_retryable(exc)
                if is_retryable and attempt < _MAX_RETRIES - 1:
                    delay = _BACKOFF_SECONDS[attempt]
                    log.warning(
                        "gemini_retry",
                        model=model,
                        attempt=attempt + 1,
                        delay_s=delay,
                        error=str(exc),
                        duration_ms=duration_ms,
                    )
                    await asyncio.sleep(delay)
                    continue

                log.error(
                    "gemini_failed",
                    model=model,
                    attempt=attempt + 1,
                    error=str(exc),
                    duration_ms=duration_ms,
                )
                raise

        # Should never reach here, but satisfy type checker
        raise last_exc  # type: ignore[misc]


def _is_retryable(exc: Exception) -> bool:
    """Check if an exception is retryable (rate-limit or transient)."""
    # google-genai raises google.genai.errors.ClientError / ServerError
    # with status codes accessible via the exception
    exc_str = str(exc).lower()
    if "429" in exc_str or "rate" in exc_str:
        return True
    if "500" in exc_str or "503" in exc_str or "timeout" in exc_str:
        return True
    if isinstance(exc, asyncio.TimeoutError):
        return True
    return False
