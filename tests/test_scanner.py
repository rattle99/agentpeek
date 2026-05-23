import dataclasses
from pathlib import Path

import pytest

from agentpeek.health import run_cross_scope_checks
from agentpeek.models import (
    MemoryFile,
    Plugin,
    PluginInstallation,
    ScanReport,
    ScanResult,
    SlashCommand,
)
from agentpeek.scanner import find_project_root, redistribute_plugins, scan
from agentpeek.sources.local import _flatten_marketplace


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
