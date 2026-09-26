"""Color sampling: extract a palette from a reference image.

Lets the model get exact hex/rgb values for a reference image instead of
eyeballing a color picker — useful for drawing/design tasks (AutoDraw, Canva,
Preview, …).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from halia.skills.base import Skill


class SampleColors(Skill):
    name = "sample_colors"
    description = (
        "Extract the dominant colors from an image file and return them as a "
        "palette of hex + rgb values, ordered by how much of the image they "
        "cover. Use this to get EXACT colors for a reference image instead of "
        "guessing in a color picker. Pass a local path to a PNG/JPG/GIF/WebP."
    )
    dangerous = False
    untrusted = True  # reads external image content
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Path to the image file."},
            "count": {
                "type": "integer",
                "description": "Number of colors to return (default: 8, max: 32).",
            },
        },
        "required": ["path"],
    }

    def run(self, args: dict[str, Any]) -> str:
        path = args.get("path", "").strip()
        if not path:
            return "error: 'path' is required"

        try:
            count = int(args.get("count", 8))
        except (TypeError, ValueError):
            count = 8
        count = max(1, min(32, count))

        try:
            from typing import cast

            from PIL import Image

            src = Path(path).expanduser()
            if not src.is_file():
                return f"error: image not found: {src}"

            img = Image.open(src).convert("RGB")
            small = img.resize((128, 128))
            quantized = small.quantize(colors=count, method=Image.Quantize.MEDIANCUT)
            palette = quantized.getpalette()
            freqs = cast(
                "list[tuple[int, float]]",
                quantized.getcolors(maxcolors=count),
            )
            if not freqs or palette is None:
                return "error: could not extract colors from the image"

            lines: list[str] = []
            for n, idx in sorted(freqs, key=lambda pair: pair[0], reverse=True):
                i = int(idx)
                r, g, b = palette[i * 3], palette[i * 3 + 1], palette[i * 3 + 2]
                lines.append(f"#{r:02x}{g:02x}{b:02x}  rgb({r},{g},{b})  ~{n}px")
            return "dominant colors:\n" + "\n".join(lines)
        except ImportError:
            return "error: Pillow is not installed"
        except Exception as exc:
            return f"error: {exc}"
