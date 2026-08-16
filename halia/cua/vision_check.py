"""Vision model detection and caching.

Checks if a model supports vision (image input) by sending a test image.
Results are cached in ~/.halia/vision_models.json so the check only happens
once per model. When the user changes models, the check runs again.

Usage:
    from halia.cua.vision_check import check_vision_support
    has_vision = check_vision_support("mimo-v2.5", provider)
"""

from __future__ import annotations

import json
from typing import Any

from halia.config.settings import CONFIG_DIR

_CACHE_FILE = CONFIG_DIR / "vision_models.json"

# 10x10 white PNG (small but valid image for vision testing)
_TEST_IMAGE_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9AAAAFklEQVQYV2P8z8BQz0BFwMgwasCo"
    "UAxMTOQCAGN1BAXbLFPoAAAAAElFTkSuQmCC"
)


def _load_cache() -> dict[str, Any]:
    """Load the vision model cache from disk."""
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
        except (json.JSONDecodeError, OSError):
            pass
    return {"vision_capable": [], "vision_disabled": []}


def _save_cache(cache: dict[str, Any]) -> None:
    """Save the vision model cache to disk."""
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _model_key(model: str, provider: str) -> str:
    """Create a unique key for model+provider combination."""
    return f"{provider}/{model}"


def check_vision_support(
    model: str,
    provider: Any,
    force_recheck: bool = False,
) -> bool:
    """Check if a model supports vision (image input).

    Sends a test image to the model. Results are cached.

    Args:
        model: Model name (e.g., "mimo-v2.5")
        provider: Provider instance with a chat() method
        force_recheck: If True, re-test even if cached

    Returns:
        True if model supports vision, False otherwise
    """
    cache = _load_cache()
    key = _model_key(model, getattr(provider, "model", model))

    # Check cache first
    if not force_recheck:
        if key in cache.get("vision_capable", []):
            return True
        if key in cache.get("vision_disabled", []):
            return False

    # Test vision support
    has_vision = _test_vision(model, provider)

    # Update cache
    if has_vision:
        if key not in cache["vision_capable"]:
            cache["vision_capable"].append(key)
        cache["vision_disabled"] = [
            k for k in cache["vision_disabled"] if k != key
        ]
    else:
        if key not in cache["vision_disabled"]:
            cache["vision_disabled"].append(key)
        cache["vision_capable"] = [
            k for k in cache["vision_capable"] if k != key
        ]

    _save_cache(cache)
    return has_vision


def _test_vision(model: str, provider: Any) -> bool:
    """Send a test image to the model to check vision support.

    Returns True if the model accepts the image, False otherwise.
    """
    try:
        # Build a message with an image
        image_message: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{_TEST_IMAGE_B64}",
                        },
                    },
                    {
                        "type": "text",
                        "text": "Reply with exactly: VISION_OK",
                    },
                ],
            }
        ]

        # Try to send via provider
        result = provider.chat(image_message)

        # If we get a response, vision is supported
        if result and hasattr(result, "content"):
            return True
        return False

    except Exception as e:
        # Log the error for debugging
        import logging
        logging.warning(f"Vision test failed for {model}: {e}")
        # Any error means vision not supported
        return False


def get_vision_status(model: str, provider_name: str = "") -> str:
    """Get the cached vision status for a model.

    Returns:
        "capable" | "disabled" | "unknown"
    """
    cache = _load_cache()
    key = _model_key(model, provider_name)

    if key in cache.get("vision_capable", []):
        return "capable"
    if key in cache.get("vision_disabled", []):
        return "disabled"
    return "unknown"


def clear_cache(model: str | None = None, provider_name: str = "") -> None:
    """Clear vision cache. If model is provided, clear only that model."""
    cache = _load_cache()

    if model:
        key = _model_key(model, provider_name)
        cache["vision_capable"] = [
            k for k in cache["vision_capable"] if k != key
        ]
        cache["vision_disabled"] = [
            k for k in cache["vision_disabled"] if k != key
        ]
    else:
        cache = {"vision_capable": [], "vision_disabled": []}

    _save_cache(cache)


def list_vision_models() -> dict[str, list[str]]:
    """List all cached vision model statuses."""
    cache = _load_cache()
    return {
        "capable": cache.get("vision_capable", []),
        "disabled": cache.get("vision_disabled", []),
    }
