"""Tests for the sample_colors skill."""

from pathlib import Path

from PIL import Image


def test_sample_colors_extracts_dominant_colors(tmp_path: Path) -> None:
    from halia.skills.sample_colors import SampleColors

    img_path = tmp_path / "red.png"
    Image.new("RGB", (16, 16), (255, 0, 0)).save(img_path)

    out = SampleColors().run({"path": str(img_path), "count": 1})
    assert "ff0000" in out
    assert "rgb(255,0,0)" in out


def test_sample_colors_requires_path() -> None:
    from halia.skills.sample_colors import SampleColors

    out = SampleColors().run({})
    assert out.startswith("error:")
    assert "path" in out


def test_sample_colors_missing_file(tmp_path: Path) -> None:
    from halia.skills.sample_colors import SampleColors

    out = SampleColors().run({"path": str(tmp_path / "nope.png")})
    assert out.startswith("error:")
    assert "not found" in out
