"""Image processor executor — handles wardrobe cataloging, receipt OCR, and visual analysis.

Runs on Node 2 where images are stored on persistent volumes.
Calls Gemini Vision API directly using the worker's GEMINI_API_KEY.
Images never leave Node 2 except for transient Gemini Vision API calls (HC-11).
"""

from __future__ import annotations

import base64
import json
import mimetypes
import time
from pathlib import Path
from typing import Any

import httpx
import structlog

from .base import BaseExecutor

log = structlog.get_logger("tars.worker.executor.image")

_IMAGE_BASE = Path("/data/wardrobe")
_PROCESS_TIMEOUT = 120  # 2 minutes max

# Gemini Vision API endpoint
_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_GEMINI_MODEL = "gemini-2.0-flash"

_WARDROBE_PROMPT = """Analyze this clothing item image and extract structured metadata.
Return a JSON object with these fields:
- item_type (str): e.g. "shirt", "pants", "jacket", "dress", "shoes", "accessory"
- color (str): Primary color(s)
- brand (str): Brand name if visible, otherwise "unknown"
- season (list[str]): Suitable seasons — "spring", "summer", "fall", "winter"
- formality (str): "casual", "smart_casual", "business_casual", "formal", "athletic"
- material (str): Best guess at material — "cotton", "polyester", "denim", "wool", "leather", "silk", "synthetic", "mixed"
- condition (str): "new", "good", "fair", "worn"
- additional_notes (str): Any other relevant details (pattern, style, etc.)

Return ONLY the JSON object, no markdown or explanation."""

_RECEIPT_PROMPT = """Extract structured data from this receipt image.
Return a JSON object with these fields:
- merchant (str): Store or restaurant name
- date (str): Date of purchase in YYYY-MM-DD format
- total (str): Total amount including currency symbol
- subtotal (str): Subtotal before tax if visible
- tax (str): Tax amount if visible
- line_items (list[dict]): Each item with "name" (str), "quantity" (int), "price" (str)
- payment_method (str): Payment method if visible

Return ONLY the JSON object, no markdown or explanation."""

_VISUAL_CONTEXT_PROMPT = """Analyze this image and provide a detailed description.
Consider: objects present, text visible, colors, context, and any notable details.
Return a JSON object with:
- description (str): Detailed description
- objects (list[str]): Key objects identified
- text_content (str): Any text visible in the image
- context (str): Inferred context or setting

Return ONLY the JSON object, no markdown or explanation."""


class ImageProcessor(BaseExecutor):
    """Processes images for wardrobe cataloging, receipt OCR, and visual analysis.

    Calls Gemini Vision API directly from Node 2. Images stay on the local
    persistent volume and are only sent transiently to the Gemini API.
    """

    EXECUTOR_TYPE = "image"

    async def execute(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Process an image task.

        Expected payload keys:
            image_path (str): Path to image on Node 2 persistent volume.
            task_type (str): "wardrobe_catalog" | "receipt_ocr" | "visual_context"
            prompt (str): Optional additional instructions to append.
            gemini_api_key (str): Gemini API key for Vision calls.
        """
        image_path = payload.get("image_path", "")
        task_type = payload.get("task_type", "visual_context")
        extra_prompt = payload.get("prompt", "")

        # Prefer API key from worker env config over payload (HC-05)
        from ..config import get_settings

        gemini_api_key = get_settings().gemini_api_key or payload.get("gemini_api_key", "")

        start = time.monotonic()

        log.info(
            "image_processor_start",
            job_id=job_id,
            task_type=task_type,
            image_path=image_path,
        )

        # Validate inputs
        if not gemini_api_key:
            return self._failure(job_id, "no gemini_api_key provided", start)

        image_file = Path(image_path)
        if not image_file.is_absolute():
            return self._failure(job_id, f"image_path must be absolute: {image_path}", start)

        if not image_file.is_file():
            return self._failure(job_id, f"image not found: {image_path}", start)

        # Security: ensure image is under allowed paths
        allowed_prefixes = [Path("/data/wardrobe"), Path("/data/outputs"), Path("/data/receipts")]
        if not any(_is_under(image_file, prefix) for prefix in allowed_prefixes):
            return self._failure(job_id, f"image path outside allowed directories: {image_path}", start)

        # Read and encode image
        try:
            image_bytes = image_file.read_bytes()
        except OSError as exc:
            return self._failure(job_id, f"failed to read image: {exc}", start)

        mime_type = mimetypes.guess_type(str(image_file))[0] or "image/jpeg"
        image_b64 = base64.b64encode(image_bytes).decode("ascii")

        # Select prompt based on task type
        prompt = _get_prompt(task_type, extra_prompt)

        # Call Gemini Vision
        try:
            result_text = await self._call_gemini_vision(
                gemini_api_key=gemini_api_key,
                image_b64=image_b64,
                mime_type=mime_type,
                prompt=prompt,
            )
        except Exception as exc:
            return self._failure(job_id, f"Gemini Vision call failed: {exc}", start)

        # Parse JSON response
        metadata = _parse_json_response(result_text)

        duration_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "image_processor_completed",
            job_id=job_id,
            task_type=task_type,
            duration_ms=duration_ms,
        )

        return {
            "status": "completed",
            "success": True,
            "job_id": job_id,
            "task_type": task_type,
            "metadata": metadata,
            "raw_response": result_text,
            "duration_ms": duration_ms,
        }

    async def _call_gemini_vision(
        self,
        *,
        gemini_api_key: str,
        image_b64: str,
        mime_type: str,
        prompt: str,
    ) -> str:
        """Call Gemini Vision API with an image and text prompt."""
        url = f"{_GEMINI_API_BASE}/{_GEMINI_MODEL}:generateContent"

        request_body = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": image_b64,
                            },
                        },
                        {"text": prompt},
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 1024,
                "responseMimeType": "application/json",
            },
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                url,
                params={"key": gemini_api_key},
                json=request_body,
            )
            resp.raise_for_status()
            data = resp.json()

        # Extract text from Gemini response
        candidates = data.get("candidates", [])
        if not candidates:
            raise ValueError("No candidates in Gemini response")

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise ValueError("No parts in Gemini response")

        return parts[0].get("text", "")

    def _failure(self, job_id: str, error: str, start: float) -> dict[str, Any]:
        """Build a failure result dict."""
        duration_ms = int((time.monotonic() - start) * 1000)
        log.error("image_processor_failed", job_id=job_id, error=error)
        return {
            "status": "failed",
            "success": False,
            "job_id": job_id,
            "error": error,
            "duration_ms": duration_ms,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_prompt(task_type: str, extra: str = "") -> str:
    """Select the system prompt based on task type."""
    prompts = {
        "wardrobe_catalog": _WARDROBE_PROMPT,
        "receipt_ocr": _RECEIPT_PROMPT,
        "visual_context": _VISUAL_CONTEXT_PROMPT,
    }
    prompt = prompts.get(task_type, _VISUAL_CONTEXT_PROMPT)
    if extra:
        prompt = f"{prompt}\n\nAdditional instructions: {extra}"
    return prompt


def _parse_json_response(text: str) -> dict[str, Any]:
    """Parse JSON from Gemini's response, handling markdown fences."""
    cleaned = text.strip()
    # Strip markdown code fences if present
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first line (```json) and last line (```)
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        log.warning("image_processor_json_parse_failed", raw=text[:200])
        return {"raw_text": text}


def _is_under(path: Path, prefix: Path) -> bool:
    """Check if path is under the given prefix directory."""
    try:
        path.resolve().relative_to(prefix.resolve())
        return True
    except ValueError:
        return False
