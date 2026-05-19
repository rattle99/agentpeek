from pathlib import Path

from agentpeek.parsers import load_frontmatter, load_json


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


def test_load_frontmatter_with_bom() -> None:
    bom = Path(__file__).parent / "fixtures" / "frontmatter" / "bom.md"
    file, warning = load_frontmatter(bom, category="test")
    assert warning is None
    assert file is not None
    # `utf-8-sig` should have stripped the U+FEFF so the frontmatter
    # parser sees a clean `---` opening fence.
    assert file.metadata.get("description") == "BOM-prefixed file"
    assert "body after BOM" in file.body


def test_load_frontmatter_list_yaml_normalized_to_empty(
    tmp_path: Path,
) -> None:
    # python-frontmatter coerces any non-mapping YAML (scalar, list,
    # null) to an empty dict — we treat this as "no frontmatter" and
    # don't warn. Documenting the behavior so a future library change
    # that breaks this contract gets caught.
    f = tmp_path / "list-fm.md"
    f.write_text("---\n- tag1\n- tag2\n---\nbody\n")
    file, warning = load_frontmatter(f, category="test")
    assert warning is None
    assert file is not None
    assert file.metadata == {}
    assert "body" in file.body
