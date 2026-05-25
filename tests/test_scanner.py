import dataclasses
import json
from pathlib import Path

import pytest

from agentpeek.health import run_cross_scope_checks
from agentpeek.models import (
    MCPServer,
    MemoryFile,
    Plugin,
    PluginInstallation,
    ScanReport,
    ScanResult,
    SlashCommand,
)
from agentpeek.scanner import find_project_root, redistribute_plugins, scan
from agentpeek.sources.local import (
    _flatten_marketplace,
    _resolve_memory_imports,
    _scan_claude_json,
)


def test_scan_local_source(sample_claude_root: Path) -> None:
    report = scan(sample_claude_root)
    result = report.project
    assert result is not None

    assert result.source == "local"
    assert result.settings is not None
    assert result.settings.model == "opus"
    assert result.settings.theme == "dark"
    assert result.settings.effort_level == "high"
    assert len(result.settings.permissions_allow) == 2
    assert result.settings.hooks_dir_files == ("orphan.sh", "present-hook.sh")

    command_names = {c.name for c in result.commands}
    assert command_names == {"good", "plain", "broken-fm"}

    by_name = {c.name: c for c in result.commands}
    assert "Sample command body" in by_name["good"].body
    assert by_name["plain"].body.startswith("# A plain command")
    assert by_name["broken-fm"].body  # raw fallback for broken frontmatter

    assert len(result.plugins) == 3
    # Memory now includes the user-level CLAUDE.md plus the auto-memory entries
    # under `projects/-fake-project/memory/`.
    assert len(result.memory) == 3
    claude_md = next(m for m in result.memory if m.kind == "claude_md")
    assert "sample memory file" in claude_md.body.lower()
    assert claude_md.project_label is None

    index = next(m for m in result.memory if m.kind == "memory_index")
    assert index.project_label == "/fake/project"
    assert index.path.name == "MEMORY.md"

    entry = next(m for m in result.memory if m.kind == "memory_entry")
    assert entry.project_label == "/fake/project"
    assert entry.has_frontmatter
    assert len(result.mcp) == 1
    assert len(result.hooks) >= 2

    assert any(not h.script_exists for h in result.hooks)

    warning_categories = [w.category for w in result.warnings]
    assert warning_categories.count("hooks") == 1
    assert warning_categories.count("commands") == 1
    assert warning_categories.count("conflicting_binding") == 1
    assert warning_categories.count("orphan_hook") == 1
    # Fixture has alpha@market-one + beta@market-two (2 installs) with
    # missing install paths (3 warnings) plus broken@market-x with no
    # installations (1 warning) → 4 plugin_state warnings.
    assert warning_categories.count("plugin_state") == 4


def test_scan_unknown_source(sample_claude_root: Path) -> None:
    report = scan(root=sample_claude_root, source_name="codex")
    result = report.project
    assert result is not None
    assert result.source == ""
    assert any(w.category == "source" for w in result.warnings)


def test_scan_undetected_root(tmp_path: Path) -> None:
    report = scan(root=tmp_path)
    result = report.project
    assert result is not None
    assert result.source == ""
    assert any(w.category == "source" for w in result.warnings)


def test_find_project_root_walks_up(tmp_path: Path) -> None:
    project = tmp_path / "project"
    deep = project / "src" / "lib"
    deep.mkdir(parents=True)
    (project / ".claude").mkdir()
    assert find_project_root(deep) == project / ".claude"


def test_find_project_root_returns_none_when_absent(tmp_path: Path) -> None:
    deep = tmp_path / "no" / "claude" / "anywhere"
    deep.mkdir(parents=True)
    assert find_project_root(deep) is None


def _make_installation(
    *, scope: str, project_path: Path | None = None
) -> PluginInstallation:
    return PluginInstallation(
        scope=scope,
        install_path=Path("/somewhere"),
        version="1.0",
        installed_at="t",
        last_updated="t",
        git_commit_sha=None,
        project_path=project_path,
    )


def _make_plugin(
    qualified_id: str,
    installations: tuple[PluginInstallation, ...],
    *,
    enabled: bool = False,
) -> Plugin:
    pid, _, marketplace = qualified_id.partition("@")
    return Plugin(
        id=pid,
        marketplace=marketplace,
        qualified_id=qualified_id,
        enabled=enabled,
        installations=installations,
    )


def _build_report(
    project_root: Path,
    user_plugins: tuple[Plugin, ...],
    project_plugins: tuple[Plugin, ...] = (),
) -> ScanReport:
    user_root = Path.home() / ".claude"
    user = dataclasses.replace(ScanResult.empty(root=user_root), plugins=user_plugins)
    project = dataclasses.replace(
        ScanResult.empty(root=project_root), plugins=project_plugins
    )
    return ScanReport(user=user, project=project, project_root=project_root)


def test_redistribute_moves_project_scope_plugin(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_root = project_dir / ".claude"
    project_root.mkdir(parents=True)
    (project_root / "settings.json").write_text('{"enabledPlugins": {"alpha@m": true}}')

    plugin = _make_plugin(
        "alpha@m",
        (_make_installation(scope="project", project_path=project_dir),),
    )
    report = _build_report(project_root, user_plugins=(plugin,))

    redistributed = redistribute_plugins(report)
    assert redistributed.user is not None
    assert redistributed.project is not None
    assert len(redistributed.user.plugins) == 0
    assert len(redistributed.project.plugins) == 1
    assert redistributed.project.plugins[0].qualified_id == "alpha@m"
    # Recomputed against project's settings.json
    assert redistributed.project.plugins[0].enabled is True


def test_redistribute_keeps_managed_scope_in_user(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_root = project_dir / ".claude"
    project_root.mkdir(parents=True)

    plugin = _make_plugin(
        "beta@m", (_make_installation(scope="managed"),), enabled=True
    )
    report = _build_report(project_root, user_plugins=(plugin,))

    redistributed = redistribute_plugins(report)
    assert redistributed.user is not None
    assert redistributed.project is not None
    assert len(redistributed.user.plugins) == 1
    assert len(redistributed.project.plugins) == 0


def test_redistribute_splits_mixed_installations(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_root = project_dir / ".claude"
    project_root.mkdir(parents=True)

    plugin = _make_plugin(
        "gamma@m",
        (
            _make_installation(scope="managed"),
            _make_installation(scope="project", project_path=project_dir),
        ),
    )
    report = _build_report(project_root, user_plugins=(plugin,))

    redistributed = redistribute_plugins(report)
    assert redistributed.user is not None
    assert redistributed.project is not None
    assert len(redistributed.user.plugins) == 1
    assert len(redistributed.user.plugins[0].installations) == 1
    assert redistributed.user.plugins[0].installations[0].scope == "managed"
    assert len(redistributed.project.plugins) == 1
    assert len(redistributed.project.plugins[0].installations) == 1
    assert redistributed.project.plugins[0].installations[0].scope == "project"


def test_redistribute_matches_local_scope_with_project_path(tmp_path: Path) -> None:
    # Real-world case: installations with scope='local' but a project_path
    # pointing at the discovered project should still move to the project scope.
    project_dir = tmp_path / "proj"
    project_root = project_dir / ".claude"
    project_root.mkdir(parents=True)

    plugin = _make_plugin(
        "delta@m",
        (_make_installation(scope="local", project_path=project_dir),),
    )
    report = _build_report(project_root, user_plugins=(plugin,))

    redistributed = redistribute_plugins(report)
    assert redistributed.user is not None
    assert redistributed.project is not None
    assert len(redistributed.user.plugins) == 0
    assert len(redistributed.project.plugins) == 1


def test_scan_no_stale_warning_for_project_scope_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: a plugin installed at project scope but registered in the
    # user-level installed_plugins.json (Claude Code's actual layout) used
    # to generate a stale "installations on disk but not enabled" warning
    # at user scope, because health checks ran before redistribute moved
    # the plugin to the project scope where it IS enabled.
    import json as _json

    user_root = tmp_path / "user" / ".claude"
    user_root.mkdir(parents=True)
    project_dir = tmp_path / "proj"
    project_root = project_dir / ".claude"
    project_root.mkdir(parents=True)

    (user_root / "plugins").mkdir()
    (user_root / "plugins" / "installed_plugins.json").write_text(
        _json.dumps(
            {
                "plugins": {
                    "foo@m": [
                        {
                            "scope": "local",
                            "projectPath": str(project_dir),
                            "installPath": str(tmp_path / "installed-foo"),
                            "version": "1.0",
                            "installedAt": "t",
                            "lastUpdated": "t",
                        }
                    ]
                }
            }
        )
    )
    (tmp_path / "installed-foo").mkdir()
    (project_root / "settings.local.json").write_text(
        _json.dumps({"enabledPlugins": {"foo@m": True}})
    )

    monkeypatch.setattr("agentpeek.scanner.USER_CLAUDE_DIR", user_root)
    monkeypatch.chdir(project_dir)

    report = scan()
    assert report.project is not None and report.user is not None
    all_warnings = list(report.user.warnings) + list(report.project.warnings)
    assert not any(
        "foo@m" in w.reason and "not enabled" in w.reason for w in all_warnings
    ), [w.reason for w in all_warnings]
    project_plugins = {p.qualified_id: p for p in report.project.plugins}
    assert "foo@m" in project_plugins
    assert project_plugins["foo@m"].enabled is True


def test_redistribute_ignores_project_path_for_other_project(tmp_path: Path) -> None:
    # An installation with a project_path pointing at some OTHER project must
    # not move into the current project's scope.
    project_dir = tmp_path / "proj"
    project_root = project_dir / ".claude"
    project_root.mkdir(parents=True)

    other_project = tmp_path / "other"
    other_project.mkdir()

    plugin = _make_plugin(
        "epsilon@m",
        (_make_installation(scope="project", project_path=other_project),),
    )
    report = _build_report(project_root, user_plugins=(plugin,))

    redistributed = redistribute_plugins(report)
    assert redistributed.user is not None
    assert redistributed.project is not None
    assert len(redistributed.user.plugins) == 1
    assert len(redistributed.project.plugins) == 0


def _make_command(name: str) -> SlashCommand:
    return SlashCommand(
        path=Path(f"/x/{name}.md"),
        name=name,
        description=None,
        argument_hint=None,
        allowed_tools=(),
        body="",
    )


def _make_memory(path: Path) -> MemoryFile:
    return MemoryFile(
        path=path,
        body="content",
        has_frontmatter=False,
        kind="claude_md",
        project_label=None,
    )


def _build_report_full(
    project_root: Path,
    *,
    user_commands: tuple[SlashCommand, ...] = (),
    project_commands: tuple[SlashCommand, ...] = (),
    user_plugins: tuple[Plugin, ...] = (),
    project_plugins: tuple[Plugin, ...] = (),
    user_memory: tuple[MemoryFile, ...] = (),
    project_memory: tuple[MemoryFile, ...] = (),
) -> ScanReport:
    user = dataclasses.replace(
        ScanResult.empty(root=Path.home() / ".claude"),
        commands=user_commands,
        plugins=user_plugins,
        memory=user_memory,
    )
    project = dataclasses.replace(
        ScanResult.empty(root=project_root),
        commands=project_commands,
        plugins=project_plugins,
        memory=project_memory,
    )
    return ScanReport(user=user, project=project, project_root=project_root)


def test_cross_scope_override_command(tmp_path: Path) -> None:
    project_root = tmp_path / "proj" / ".claude"
    project_root.mkdir(parents=True)
    report = _build_report_full(
        project_root,
        user_commands=(_make_command("shared"), _make_command("user-only")),
        project_commands=(_make_command("shared"),),
    )
    issues = run_cross_scope_checks(report)
    categories = [w.category for w in issues]
    assert categories.count("scope_override_command") == 1


def test_cross_scope_override_plugin(tmp_path: Path) -> None:
    project_root = tmp_path / "proj" / ".claude"
    project_root.mkdir(parents=True)
    inst_user = _make_installation(scope="managed")
    inst_project = _make_installation(scope="project", project_path=project_root.parent)
    report = _build_report_full(
        project_root,
        user_plugins=(_make_plugin("p@m", (inst_user,)),),
        project_plugins=(_make_plugin("p@m", (inst_project,)),),
    )
    issues = run_cross_scope_checks(report)
    categories = [w.category for w in issues]
    assert categories.count("scope_override_plugin") == 1


def test_cross_scope_layered_memory(tmp_path: Path) -> None:
    project_root = tmp_path / "proj" / ".claude"
    project_root.mkdir(parents=True)
    report = _build_report_full(
        project_root,
        user_memory=(_make_memory(Path.home() / ".claude" / "CLAUDE.md"),),
        project_memory=(_make_memory(project_root / "CLAUDE.md"),),
    )
    issues = run_cross_scope_checks(report)
    categories = [w.category for w in issues]
    assert categories.count("scope_layered_memory") == 1


def test_cross_scope_no_overlap_no_warnings(tmp_path: Path) -> None:
    project_root = tmp_path / "proj" / ".claude"
    project_root.mkdir(parents=True)
    report = _build_report_full(
        project_root,
        user_commands=(_make_command("only-user"),),
        project_commands=(_make_command("only-project"),),
    )
    assert run_cross_scope_checks(report) == []


def test_flatten_marketplace_captures_auto_update_true() -> None:
    flat = _flatten_marketplace(
        {
            "source": {"source": "github", "repo": "owner/repo"},
            "installLocation": "/cache/repo",
            "lastUpdated": "2026-05-01T00:00:00Z",
            "autoUpdate": True,
        }
    )
    assert flat["autoUpdate"] == "true"


def test_flatten_marketplace_captures_auto_update_false() -> None:
    flat = _flatten_marketplace(
        {
            "source": {"source": "github", "repo": "owner/repo"},
            "autoUpdate": False,
        }
    )
    assert flat["autoUpdate"] == "false"


def test_flatten_marketplace_omits_auto_update_when_absent() -> None:
    flat = _flatten_marketplace(
        {
            "source": {"source": "github", "repo": "owner/repo"},
            "installLocation": "/cache/repo",
        }
    )
    assert "autoUpdate" not in flat


# --- v0.13: agents loader ----------------------------------------------


def _make_claude_root(tmp_path: Path) -> Path:
    """Build a minimal .claude/ root that detect() accepts."""
    root = tmp_path / ".claude"
    root.mkdir(parents=True)
    # detect() returns False on an empty dir; a settings.local.json
    # alone is the cheapest indicator.
    (root / "settings.local.json").write_text("{}")
    return root


def test_scan_agents_user_scope_minimal(tmp_path: Path) -> None:
    root = _make_claude_root(tmp_path)
    agents_dir = root / "agents"
    agents_dir.mkdir()
    (agents_dir / "minimal.md").write_text(
        "---\nname: minimal\ndescription: a tiny agent\n---\n\nDo a thing."
    )
    report = scan(root=root)
    result = report.project or report.user
    assert result is not None
    assert len(result.agents) == 1
    agent = result.agents[0]
    assert agent.name == "minimal"
    assert agent.description == "a tiny agent"
    assert "Do a thing." in agent.body
    assert agent.tools == ()
    assert agent.model is None


def test_scan_agents_full_frontmatter(tmp_path: Path) -> None:
    root = _make_claude_root(tmp_path)
    agents_dir = root / "agents"
    agents_dir.mkdir()
    (agents_dir / "code-reviewer.md").write_text(
        "---\n"
        "name: code-reviewer\n"
        "description: Reviews code\n"
        "tools: Read, Grep, Glob\n"
        "disallowedTools: Write, Edit\n"
        "model: sonnet\n"
        "permissionMode: plan\n"
        "memory: project\n"
        "background: true\n"
        "color: purple\n"
        "skills:\n"
        "  - api-conventions\n"
        "  - error-handling\n"
        "mcpServers:\n"
        "  - github\n"
        "hooks:\n"
        "  PreToolUse: []\n"
        "---\n\nYou are a code reviewer."
    )
    report = scan(root=root)
    result = report.project or report.user
    assert result is not None
    agent = result.agents[0]
    assert agent.tools == ("Read", "Grep", "Glob")
    assert agent.disallowed_tools == ("Write", "Edit")
    assert agent.model == "sonnet"
    assert agent.permission_mode == "plan"
    assert agent.memory == "project"
    assert agent.background is True
    assert agent.color == "purple"
    assert agent.skills == ("api-conventions", "error-handling")
    assert agent.has_mcp_servers is True
    assert agent.has_hooks is True


def test_scan_agents_recursive_subdirs(tmp_path: Path) -> None:
    root = _make_claude_root(tmp_path)
    nested = root / "agents" / "review" / "deep"
    nested.mkdir(parents=True)
    (nested / "security.md").write_text(
        "---\nname: review-security\ndescription: deep-nested agent\n---\nbody"
    )
    report = scan(root=root)
    result = report.project or report.user
    assert result is not None
    assert len(result.agents) == 1
    assert result.agents[0].name == "review-security"


def test_scan_agents_falls_back_when_frontmatter_missing(tmp_path: Path) -> None:
    root = _make_claude_root(tmp_path)
    agents_dir = root / "agents"
    agents_dir.mkdir()
    # No frontmatter at all — name should fall back to the filename stem.
    (agents_dir / "bare.md").write_text("just a body, no fm")
    report = scan(root=root)
    result = report.project or report.user
    assert result is not None
    assert len(result.agents) == 1
    assert result.agents[0].name == "bare"


# --- v0.13: rules loader ----------------------------------------------


def test_scan_rules_path_scoped(tmp_path: Path) -> None:
    root = _make_claude_root(tmp_path)
    rules_dir = root / "rules"
    rules_dir.mkdir()
    (rules_dir / "api.md").write_text(
        "---\n"
        "paths:\n"
        "  - 'src/api/**/*.ts'\n"
        "  - 'lib/**/*.ts'\n"
        "---\n"
        "API validation rules"
    )
    report = scan(root=root)
    result = report.project or report.user
    assert result is not None
    assert len(result.rules) == 1
    rule = result.rules[0]
    assert rule.paths_globs == ("src/api/**/*.ts", "lib/**/*.ts")
    assert rule.always_loaded is False
    assert "API validation rules" in rule.body


def test_scan_rules_always_loaded(tmp_path: Path) -> None:
    root = _make_claude_root(tmp_path)
    rules_dir = root / "rules"
    rules_dir.mkdir()
    (rules_dir / "global.md").write_text("no frontmatter at all")
    report = scan(root=root)
    result = report.project or report.user
    assert result is not None
    rule = result.rules[0]
    assert rule.paths_globs == ()
    assert rule.always_loaded is True


def test_scan_rules_recursive(tmp_path: Path) -> None:
    root = _make_claude_root(tmp_path)
    nested = root / "rules" / "frontend"
    nested.mkdir(parents=True)
    (nested / "react.md").write_text("---\npaths: ['**/*.tsx']\n---\nuse hooks")
    report = scan(root=root)
    result = report.project or report.user
    assert result is not None
    assert len(result.rules) == 1
    assert result.rules[0].name == "frontend/react"


# --- v0.13: CLAUDE.local.md -------------------------------------------


def test_scan_memory_picks_up_claude_local_md(tmp_path: Path) -> None:
    # Per docs, CLAUDE.local.md sits at project root (one above .claude/).
    project = tmp_path / "proj"
    root = project / ".claude"
    root.mkdir(parents=True)
    (root / "settings.local.json").write_text("{}")
    (project / "CLAUDE.local.md").write_text("personal override")
    report = scan(root=root)
    result = report.project or report.user
    assert result is not None
    local = [m for m in result.memory if m.kind == "claude_local_md"]
    assert len(local) == 1
    assert "personal override" in local[0].body


# --- v0.13: @path imports ----------------------------------------------


def test_resolve_memory_imports_single(tmp_path: Path) -> None:
    target = tmp_path / "imported.md"
    target.write_text("imported body")
    parent = tmp_path / "CLAUDE.md"
    parent.write_text("see @imported.md")
    imports = _resolve_memory_imports(parent, parent.read_text())
    assert len(imports) == 1
    assert imports[0].raw == "imported.md"
    assert imports[0].resolved_path == target.resolve()
    assert imports[0].reason is None


def test_resolve_memory_imports_recursive(tmp_path: Path) -> None:
    leaf = tmp_path / "leaf.md"
    leaf.write_text("leaf body")
    mid = tmp_path / "mid.md"
    mid.write_text("see @leaf.md")
    root = tmp_path / "root.md"
    root.write_text("see @mid.md")
    imports = _resolve_memory_imports(root, root.read_text())
    assert len(imports) == 2
    assert imports[0].depth == 1
    assert imports[1].depth == 2


def test_resolve_memory_imports_cycle(tmp_path: Path) -> None:
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("see @b.md")
    b.write_text("see @a.md")
    imports = _resolve_memory_imports(a, a.read_text())
    # b should resolve cleanly; b's import back to a should be a cycle.
    cycles = [i for i in imports if i.reason == "cycle"]
    assert len(cycles) == 1


def test_resolve_memory_imports_missing(tmp_path: Path) -> None:
    parent = tmp_path / "CLAUDE.md"
    parent.write_text("see @nonexistent.md")
    imports = _resolve_memory_imports(parent, parent.read_text())
    assert len(imports) == 1
    assert imports[0].reason == "missing"


def test_resolve_memory_imports_home_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "global.md").write_text("home body")
    parent = tmp_path / "CLAUDE.md"
    parent.write_text("see @~/global.md")
    imports = _resolve_memory_imports(parent, parent.read_text())
    assert len(imports) == 1
    assert imports[0].reason is None


# --- v0.13: ~/.claude.json --------------------------------------------


def test_scan_claude_json_surfaces_user_mcp_servers(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {"command": "gh", "args": ["mcp"]},
                },
                "projects": {},
            }
        )
    )
    mcp_results: list[MCPServer] = []
    seen_names: set[str] = set()
    trust, oauth, recorded = _scan_claude_json(path, mcp_results, seen_names, [])
    assert recorded == path
    assert any(m.name == "github" for m in mcp_results)
    assert trust == ()
    assert oauth is False


def test_scan_claude_json_trust_entries(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    path.write_text(
        json.dumps(
            {
                "projects": {
                    "/Users/me/repo": {
                        "hasTrustDialogAccepted": True,
                        "allowedTools": ["Read", "Grep"],
                        "enabledMcpjsonServers": ["github"],
                        "disabledMcpjsonServers": [],
                    }
                }
            }
        )
    )
    trust, _, _ = _scan_claude_json(path, [], set(), [])
    assert len(trust) == 1
    entry = trust[0]
    assert entry.project_path == "/Users/me/repo"
    assert entry.trust_accepted is True
    assert entry.allowed_tools == ("Read", "Grep")
    assert entry.enabled_mcpjson_servers == ("github",)
    assert entry.disabled_mcpjson_servers == ()


def test_scan_claude_json_oauth_presence_only(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    # Non-empty oauthAccount should flip presence to True without
    # the test reading the value.
    path.write_text(
        json.dumps({"oauthAccount": {"emailAddress": "redacted"}})
    )
    _, oauth, _ = _scan_claude_json(path, [], set(), [])
    assert oauth is True


def test_scan_claude_json_oauth_absent(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    path.write_text(json.dumps({"oauthAccount": {}}))
    _, oauth, _ = _scan_claude_json(path, [], set(), [])
    assert oauth is False


# --- v0.13: settings extensions ---------------------------------------


def test_scan_settings_surfaces_default_agent(tmp_path: Path) -> None:
    root = _make_claude_root(tmp_path)
    (root / "settings.json").write_text(
        json.dumps({"agent": "code-reviewer"})
    )
    report = scan(root=root)
    result = report.project or report.user
    assert result is not None
    assert result.settings is not None
    assert result.settings.default_agent == "code-reviewer"


def test_scan_settings_surfaces_claude_md_excludes(tmp_path: Path) -> None:
    root = _make_claude_root(tmp_path)
    (root / "settings.json").write_text(
        json.dumps({"claudeMdExcludes": ["**/other-team/CLAUDE.md"]})
    )
    report = scan(root=root)
    result = report.project or report.user
    assert result is not None
    assert result.settings is not None
    assert result.settings.claude_md_excludes == ("**/other-team/CLAUDE.md",)


# --- v0.13: agent-memory directories ----------------------------------


def test_scan_agent_memory_user_scope(tmp_path: Path) -> None:
    root = _make_claude_root(tmp_path)
    mem = root / "agent-memory" / "code-reviewer"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("# Code reviewer memory")
    (mem / "topic.md").write_text("# topic body")
    report = scan(root=root)
    result = report.project or report.user
    assert result is not None
    agent_memories = [
        m for m in result.memory
        if m.kind in ("agent_memory_index", "agent_memory_entry")
    ]
    assert len(agent_memories) == 2
    assert all(m.agent_name == "code-reviewer" for m in agent_memories)
    index = next(m for m in agent_memories if m.kind == "agent_memory_index")
    assert index.project_label == "agent-memory"


def test_scan_agent_memory_local_scope(tmp_path: Path) -> None:
    root = _make_claude_root(tmp_path)
    mem = root / "agent-memory-local" / "debugger"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("# local agent memory")
    report = scan(root=root)
    result = report.project or report.user
    assert result is not None
    locals_ = [m for m in result.memory if m.project_label == "agent-memory-local"]
    assert len(locals_) == 1
    assert locals_[0].agent_name == "debugger"
