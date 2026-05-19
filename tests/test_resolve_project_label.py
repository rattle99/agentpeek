import json
from pathlib import Path

from agentpeek.sources.local import _resolve_project_label


def _write_jsonl(path: Path, lines: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")


def test_returns_cwd_from_first_jsonl(tmp_path: Path) -> None:
    _resolve_project_label.cache_clear()
    proj = tmp_path / "-Users-x-project"
    proj.mkdir()
    _write_jsonl(
        proj / "session-1.jsonl",
        [{"role": "user", "cwd": "/Users/x/project"}],
    )
    assert _resolve_project_label(proj) == "/Users/x/project"


def test_skips_corrupt_lines_within_a_jsonl(tmp_path: Path) -> None:
    _resolve_project_label.cache_clear()
    proj = tmp_path / "-Users-x-project"
    proj.mkdir()
    (proj / "session-1.jsonl").write_text(
        "not json at all\n"
        + json.dumps({"role": "system"})
        + "\n"
        + json.dumps({"role": "user", "cwd": "/Users/x/project"})
        + "\n"
    )
    assert _resolve_project_label(proj) == "/Users/x/project"


def test_falls_through_to_next_jsonl_when_first_has_no_cwd(
    tmp_path: Path,
) -> None:
    _resolve_project_label.cache_clear()
    proj = tmp_path / "-Users-x-project"
    proj.mkdir()
    # Lexicographic order matters — sorted() picks session-1 first.
    _write_jsonl(
        proj / "session-1.jsonl",
        [{"role": "system"}, {"role": "user"}],
    )
    _write_jsonl(
        proj / "session-2.jsonl",
        [{"role": "user", "cwd": "/Users/x/project"}],
    )
    assert _resolve_project_label(proj) == "/Users/x/project"


def test_falls_back_to_dir_name_when_no_jsonl(tmp_path: Path) -> None:
    _resolve_project_label.cache_clear()
    proj = tmp_path / "-Users-x-project"
    proj.mkdir()
    # No session logs — fall back to the encoded form so the user at
    # least sees the directory name.
    assert _resolve_project_label(proj) == "-Users-x-project"


def test_falls_back_when_all_jsonl_lack_cwd(tmp_path: Path) -> None:
    _resolve_project_label.cache_clear()
    proj = tmp_path / "-Users-x-project"
    proj.mkdir()
    _write_jsonl(proj / "session-1.jsonl", [{"role": "system"}])
    _write_jsonl(proj / "session-2.jsonl", [{"role": "user"}])
    assert _resolve_project_label(proj) == "-Users-x-project"


def test_falls_back_when_all_jsonl_are_corrupt(tmp_path: Path) -> None:
    _resolve_project_label.cache_clear()
    proj = tmp_path / "-Users-x-project"
    proj.mkdir()
    (proj / "session-1.jsonl").write_text("garbage line one\ngarbage line two\n")
    (proj / "session-2.jsonl").write_text("more garbage\n")
    assert _resolve_project_label(proj) == "-Users-x-project"
