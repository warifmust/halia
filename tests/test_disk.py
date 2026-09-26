"""Tests for the read-only disk_usage skill (df + du, no shell)."""

from typing import Any

from halia.skills.disk import DiskUsage, _human


def test_human_sizes() -> None:
    assert _human(0) == "0B"
    assert _human(512) == "512B"
    assert _human(1024) == "1.0K"
    assert _human(82 * 1024**3 + 400 * 1024**2) == "82.4G"


def test_disk_usage_runs_fixed_df_and_du(monkeypatch: Any, tmp_path: Any) -> None:
    """df/du run with fixed argv (no shell), du is parsed and ranked, stderr dropped."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        calls.append(cmd)
        out = {
            "df": "Filesystem      Size  Used Avail Capacity Mounted on\n"
                  "/dev/disk3s1   926Gi  11Gi 821Gi     2%   /",
            "du": "84508688\t/Users/x/docker\n"
                  "1048576\t/Users/x/Works\n"
                  "2048\t/Users/x/small",
        }[cmd[0]]
        err = "du: /Users/x/Library: Operation not permitted" if cmd[0] == "du" else ""
        return _FakeProc(stdout=out, stderr=err, returncode=0)

    monkeypatch.setattr("halia.skills.disk.subprocess.run", fake_run)

    target = tmp_path / "inspect"
    target.mkdir()
    out = DiskUsage().run({"path": str(target), "depth": 1, "top": 2})

    # argv is fixed and shell-free.
    assert ["df", "-h", str(target)] in calls
    assert ["du", "-x", "-d", "1", "-k", str(target)] in calls

    assert "volume (df -h)" in out
    assert "largest 2 under" in out
    assert "80.6G" in out  # 84508688 KiB ≈ 80.6G
    assert "/Users/x/docker" in out
    assert "/Users/x/small" not in out  # ranked out of top-2
    # stderr (permission noise) is dropped, not surfaced.
    assert "Operation not permitted" not in out


def test_disk_usage_is_safe_and_default_registered() -> None:
    from halia.skills import available_skills, default_registry

    assert DiskUsage().dangerous is False
    assert "disk_usage" in available_skills()
    assert default_registry().get("disk_usage") is not None


def test_disk_usage_rejects_missing_path() -> None:
    assert DiskUsage().run({"path": "/does/not/exist"}).startswith("error:")


class _FakeProc:
    def __init__(self, stdout: str, stderr: str, returncode: int) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
