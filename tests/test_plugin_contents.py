from pathlib import Path

from agentview.parsers.plugin_contents import parse_plugin_contents

FIXTURE = Path(__file__).parent / "fixtures" / "plugins" / "sample-plugin"


def test_parse_manifest_basic_fields() -> None:
    contents = parse_plugin_contents(FIXTURE, qualified_id="sample-plugin@market")
    assert contents.manifest is not None
    m = contents.manifest
    assert m.description and "synthetic" in m.description
    assert m.version == "0.1.0"
    assert m.author_name == "Test Author"
    assert m.author_email == "test@example.com"
    assert m.license == "MIT"
    assert m.homepage == "https://example.com/sample-plugin"
    assert m.keywords == ("test", "fixture")


def test_parse_skills_lists_both_with_frontmatter() -> None:
    contents = parse_plugin_contents(FIXTURE, qualified_id="sample-plugin@market")
    names = sorted(s.name for s in contents.skills)
    assert names == ["brainstorm", "think-hard"]
    by_name = {s.name: s for s in contents.skills}
    assert by_name["think-hard"].description == "Think harder before acting"
    assert "Take a step back" in by_name["think-hard"].body


def test_parse_agents() -> None:
    contents = parse_plugin_contents(FIXTURE, qualified_id="sample-plugin@market")
    assert len(contents.agents) == 1
    a = contents.agents[0]
    assert a.name == "reviewer"
    assert a.description == "Reviews code changes"


def test_parse_commands_stamps_source_plugin() -> None:
    contents = parse_plugin_contents(FIXTURE, qualified_id="sample-plugin@market")
    assert len(contents.commands) == 1
    cmd = contents.commands[0]
    assert cmd.name == "run-tests"
    assert cmd.description == "Run the test suite"
    assert cmd.allowed_tools == ("Bash",)
    assert cmd.source_plugin == "sample-plugin@market"


def test_parse_hooks_stamps_source_plugin() -> None:
    contents = parse_plugin_contents(FIXTURE, qualified_id="sample-plugin@market")
    assert len(contents.hooks) == 1
    h = contents.hooks[0]
    assert h.event == "PreToolUse"
    assert h.matcher == "Bash"
    assert h.command == "echo before-bash"
    assert h.source_plugin == "sample-plugin@market"


def test_parse_mcps_from_manifest_stamps_source_plugin() -> None:
    contents = parse_plugin_contents(FIXTURE, qualified_id="sample-plugin@market")
    assert len(contents.mcps) == 1
    s = contents.mcps[0]
    assert s.name == "sample-mcp"
    assert s.command == "echo"
    assert s.args == ("mcp",)
    assert s.source_plugin == "sample-plugin@market"


def test_parse_missing_install_path_returns_empty(tmp_path: Path) -> None:
    # An install path that exists but has nothing in it — every sub-list
    # should be empty and warnings should be empty (no failures, just
    # nothing to enumerate).
    empty = tmp_path / "no-such-plugin"
    empty.mkdir()
    contents = parse_plugin_contents(empty, qualified_id="ghost@m")
    assert contents.manifest is None
    assert contents.skills == ()
    assert contents.agents == ()
    assert contents.commands == ()
    assert contents.hooks == ()
    assert contents.mcps == ()
    assert contents.warnings == ()


def test_parse_malformed_manifest_emits_warning(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "broken-manifest"
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text("not valid json")
    contents = parse_plugin_contents(plugin_dir, qualified_id="broken@m")
    assert contents.manifest is None
    assert any(
        w.category == "plugin_manifest" for w in contents.warnings
    ), f"expected plugin_manifest warning, got {contents.warnings}"
