"""Single place that picks an LLM provider based on which API key is set,
so classify.py and brief_generator.py don't each need their own
provider-specific code.

Provider selection:
  - CIVIC_LLM_PROVIDER=gemini    force Gemini (needs GEMINI_API_KEY)
  - CIVIC_LLM_PROVIDER=anthropic force Claude (needs ANTHROPIC_API_KEY)
  - unset -> auto: Gemini if GEMINI_API_KEY is set, else Claude if
    ANTHROPIC_API_KEY is set, else no LLM features (keyword-only fallback).

Every function here returns None on any failure (missing key, missing
package, network error, bad response) instead of raising - callers must
treat None as "no LLM available right now", never fabricate a result.
"""
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

GEMINI_MODEL = os.environ.get("CIVIC_GEMINI_MODEL", "gemini-3.6-flash")
CLAUDE_MODEL = os.environ.get("CIVIC_LLM_MODEL", "claude-sonnet-4-6")

# If the main Gemini model is overloaded (HTTP 503) or rate-limited (429), each of
# these is tried in order. Override with CIVIC_GEMINI_FALLBACK_MODELS=model1,model2
GEMINI_FALLBACK_MODELS = [m.strip() for m in os.environ.get(
    "CIVIC_GEMINI_FALLBACK_MODELS", "gemini-3.5-flash-lite,gemini-3.5-flash,gemini-3.6-flash").split(",") if m.strip()]
GEMINI_RETRIES = int(os.environ.get("CIVIC_GEMINI_RETRIES", "2"))  # attempts per model


def _forced_provider() -> Optional[str]:
    p = os.environ.get("CIVIC_LLM_PROVIDER", "").strip().lower()
    return p if p in ("gemini", "anthropic") else None


def active_provider() -> Optional[str]:
    """Which provider would actually be used right now, or None."""
    forced = _forced_provider()
    if forced == "gemini":
        return "gemini" if os.environ.get("GEMINI_API_KEY") else None
    if forced == "anthropic":
        return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else None
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def available() -> bool:
    provider = active_provider()
    if provider is None:
        return False
    try:
        if provider == "gemini":
            import google.genai  # noqa: F401
        else:
            import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def complete(prompt: str, max_tokens: int = 300, image_jpeg_b64: Optional[str] = None) -> Optional[str]:
    """Send a plain-text prompt (optionally with one base64 JPEG image) to
    whichever provider is active, and return its raw text response, or
    None on any failure."""
    provider = active_provider()
    if provider is None:
        return None
    try:
        if provider == "gemini":
            return _complete_gemini(prompt, max_tokens, image_jpeg_b64)
        return _complete_claude(prompt, max_tokens, image_jpeg_b64)
    except Exception:
        logger.exception("LLM call failed (provider=%s)", provider)
        return None


def _generate_with_retry(client, contents, config, plain_config=None):
    """Call Gemini, retrying temporary failures (503 overloaded, 429 rate limit,
    other 5xx) with a growing pause, then falling back to the next model in
    GEMINI_FALLBACK_MODELS. A model that doesn't exist (404) is skipped straight
    away. Other errors (bad key, bad request) are raised immediately since
    retrying won't help."""
    from google.genai import errors
    models = [GEMINI_MODEL] + [m for m in GEMINI_FALLBACK_MODELS if m != GEMINI_MODEL]
    last = None
    for model in models:
        for attempt in range(GEMINI_RETRIES):
            try:
                if model != GEMINI_MODEL or attempt:
                    logger.warning("Gemini: trying model=%s (attempt %d)", model, attempt + 1)
                return client.models.generate_content(model=model, contents=contents, config=config)
            except errors.APIError as e:
                code = getattr(e, "code", None)
                last = e
                if code == 400 and plain_config is not None and config is not plain_config:
                    # Some models reject the thinking_budget setting (older 2.5-style knob).
                    # Retry this same model once with a plain config.
                    logger.warning("Gemini: %s rejected the request config (400) - retrying without thinking settings", model)
                    config = plain_config
                    continue
                if code in (404, 429):
                    break                                   # unknown model / quota hit -> next model now
                if code is not None and code >= 500:
                    time.sleep(2 * (attempt + 1))           # overloaded: wait 2s, 4s, 6s and retry
                    continue
                raise                                       # key/request problem: don't retry
    raise last


def _complete_gemini(prompt: str, max_tokens: int, image_jpeg_b64: Optional[str]) -> str:
    import base64
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    contents = [prompt]
    if image_jpeg_b64:
        contents.append(types.Part.from_bytes(data=base64.b64decode(image_jpeg_b64), mime_type="image/jpeg"))
    config = types.GenerateContentConfig(
        max_output_tokens=max_tokens,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    plain_config = types.GenerateContentConfig(max_output_tokens=max_tokens)
    response = _generate_with_retry(client, contents, config, plain_config)
    return response.text.strip()


def _complete_claude(prompt: str, max_tokens: int, image_jpeg_b64: Optional[str]) -> str:
    import anthropic
    client = anthropic.Anthropic()
    content = []
    if image_jpeg_b64:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_jpeg_b64}})
    content.append({"type": "text", "text": prompt})
    response = client.messages.create(model=CLAUDE_MODEL, max_tokens=max_tokens,
                                       messages=[{"role": "user", "content": content}])
    return "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()


def strip_json_fences(text: str) -> str:
    return text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()