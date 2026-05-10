from pathlib import Path

from agentview.parsers import load_frontmatter, load_json


def test_load_json_broken(sample_claude_root: Path) -> None:
    broken = sample_claude_root / "settings.broken.json"
    data, warning = load_json(broken, category="test")
    assert data is None
    assert warning is not None
    assert warning.category == "test"
    assert "JSON" in warning.reason or "json" in warning.reason


def test_load_json_missing(tmp_path: Path) -> None:
    data, warning = load_json(tmp_path / "missing.json", category="test")
    assert data is None
    assert warning is not None
    assert warning.category == "test"


def test_load_json_valid(sample_claude_root: Path) -> None:
    data, warning = load_json(sample_claude_root / "settings.json", category="test")
    assert warning is None
    assert isinstance(data, dict)


def test_load_frontmatter_with_frontmatter(sample_claude_root: Path) -> None:
    good = sample_claude_root / "commands" / "good.md"
    file, warning = load_frontmatter(good, category="test")
    assert warning is None
    assert file is not None
    assert file.metadata.get("description") == "Sample slash command"


def test_load_frontmatter_no_frontmatter(sample_claude_root: Path) -> None:
    plain = sample_claude_root / "commands" / "plain.md"
    file, warning = load_frontmatter(plain, category="test")
    assert warning is None
    assert file is not None
    assert file.metadata == {}
    assert len(file.body) > 0


def test_load_frontmatter_broken(sample_claude_root: Path) -> None:
    broken = sample_claude_root / "commands" / "broken-fm.md"
    file, warning = load_frontmatter(broken, category="test")
    assert file is None
    assert warning is not None
    assert warning.category == "test"
