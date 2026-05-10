from pathlib import Path

from agentview.scanner import scan


def test_scan_local_source(sample_claude_root: Path) -> None:
    result = scan(sample_claude_root)

    assert result.source == "local"
    assert result.settings is not None
    assert result.settings.model == "opus"
    assert result.settings.theme == "dark"
    assert result.settings.effort_level == "high"
    assert len(result.settings.permissions_allow) == 2
    assert result.settings.hooks_dir_files == 2

    command_names = {c.name for c in result.commands}
    assert command_names == {"good", "plain", "broken-fm"}

    assert len(result.plugins) == 2
    assert len(result.memory) == 1
    assert len(result.mcp) == 1
    assert len(result.hooks) >= 2

    assert any(not h.script_exists for h in result.hooks)

    warning_categories = [w.category for w in result.warnings]
    assert warning_categories.count("hooks") == 1
    assert warning_categories.count("commands") == 1


def test_scan_unknown_source() -> None:
    result = scan(source_name="codex")
    assert result.source == ""
    assert len(result.warnings) == 1
    assert result.warnings[0].category == "source"


def test_scan_undetected_root(tmp_path: Path) -> None:
    result = scan(root=tmp_path)
    assert result.source == ""
    assert any(w.category == "source" for w in result.warnings)
