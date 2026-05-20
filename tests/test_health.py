from pathlib import Path

from agentpeek.health import (
    _check_conflicting_keybindings,
    _check_orphan_hooks,
    _check_plugin_state,
)
from agentpeek.models import (
    HookSpec,
    KeybindingEntry,
    KeybindingsBundle,
    Plugin,
    PluginInstallation,
    ScanResult,
)


def _result(
    plugins: tuple[Plugin, ...] = (),
    *,
    root: Path = Path("/tmp/.claude"),
    hooks: tuple[HookSpec, ...] = (),
    keybindings: KeybindingsBundle | None = None,
) -> ScanResult:
    return ScanResult(
        source="local",
        root=root,
        settings=None,
        hooks=hooks,
        commands=(),
        plugins=plugins,
        memory=(),
        keybindings=keybindings,
        mcp=(),
        warnings=(),
    )


def _plugin(
    qid: str, *, enabled: bool, installations: tuple[PluginInstallation, ...]
) -> Plugin:
    return Plugin(
        id=qid.split("@")[0],
        marketplace=qid.split("@")[1],
        qualified_id=qid,
        enabled=enabled,
        installations=installations,
    )


def _install(path: Path) -> PluginInstallation:
    return PluginInstallation(
        scope="managed",
        install_path=path,
        version="1.0.0",
        installed_at="2026-01-01T00:00:00Z",
        last_updated="2026-01-01T00:00:00Z",
        git_commit_sha=None,
        project_path=None,
    )


def test_enabled_but_no_installations_flagged() -> None:
    issues = _check_plugin_state(
        _result((_plugin("foo@m", enabled=True, installations=()),))
    )
    assert len(issues) == 1
    assert "no installations" in issues[0].reason


def test_disabled_with_installations_flagged(tmp_path: Path) -> None:
    installed = tmp_path / "installed-plugin"
    installed.mkdir()
    issues = _check_plugin_state(
        _result(
            (
                _plugin(
                    "foo@m",
                    enabled=False,
                    installations=(_install(installed),),
                ),
            )
        )
    )
    assert len(issues) == 1
    assert "not enabled" in issues[0].reason


def test_missing_install_path_flagged() -> None:
    issues = _check_plugin_state(
        _result(
            (
                _plugin(
                    "foo@m",
                    enabled=True,
                    installations=(_install(Path("/does/not/exist")),),
                ),
            )
        )
    )
    assert len(issues) == 1
    assert "missing install path" in issues[0].reason


def test_other_project_installs_dont_trigger_not_enabled_warning(
    tmp_path: Path,
) -> None:
    # A plugin whose only installations belong to OTHER projects (their
    # projectPath doesn't match this scope's project root) lives in
    # user.plugins after redistribute, but we can't read those projects'
    # settings to know whether the plugin is enabled there. Don't emit
    # a misleading "installed but not enabled" warning.
    elsewhere = tmp_path / "some-other-project"
    elsewhere.mkdir()
    inst_path = tmp_path / "installed"
    inst_path.mkdir()
    inst = PluginInstallation(
        scope="project",
        install_path=inst_path,
        version="1.0",
        installed_at="t",
        last_updated="t",
        git_commit_sha=None,
        project_path=elsewhere,
    )
    issues = _check_plugin_state(
        _result(
            (_plugin("foo@m", enabled=False, installations=(inst,)),),
            root=tmp_path / ".claude",
        )
    )
    assert issues == []


def test_mixed_user_and_other_project_installs_still_warns(
    tmp_path: Path,
) -> None:
    # If at least one installation IS user-level (project_path=None),
    # the plugin is meaningfully "installed at this scope" and the
    # not-enabled warning should still fire.
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    user_inst = _install(tmp_path / "u")
    (tmp_path / "u").mkdir()
    project_inst = PluginInstallation(
        scope="project",
        install_path=tmp_path / "p",
        version="1.0",
        installed_at="t",
        last_updated="t",
        git_commit_sha=None,
        project_path=elsewhere,
    )
    (tmp_path / "p").mkdir()
    issues = _check_plugin_state(
        _result(
            (
                _plugin(
                    "foo@m",
                    enabled=False,
                    installations=(user_inst, project_inst),
                ),
            ),
            root=tmp_path / ".claude",
        )
    )
    not_enabled = [i for i in issues if "not enabled" in i.reason]
    assert len(not_enabled) == 1


def test_healthy_plugin_no_warnings(tmp_path: Path) -> None:
    installed = tmp_path / "ok-plugin"
    installed.mkdir()
    issues = _check_plugin_state(
        _result(
            (
                _plugin(
                    "foo@m",
                    enabled=True,
                    installations=(_install(installed),),
                ),
            )
        )
    )
    assert issues == []


# --- _check_orphan_hooks ----------------------------------------------


def _hook(referenced: Path | None) -> HookSpec:
    return HookSpec(
        event="PreToolUse",
        matcher=None,
        type="command",
        command="bash x.sh" if referenced else "echo hi",
        timeout=None,
        referenced_script=referenced,
        script_exists=referenced is not None and referenced.exists(),
    )


def test_orphan_hook_detected_when_unreferenced(tmp_path: Path) -> None:
    root = tmp_path
    hooks_dir = root / "hooks"
    hooks_dir.mkdir()
    referenced = hooks_dir / "used.sh"
    referenced.write_text("#!/bin/sh\n")
    orphan = hooks_dir / "orphan.sh"
    orphan.write_text("#!/bin/sh\n")
    issues = _check_orphan_hooks(
        _result(root=root, hooks=(_hook(referenced),))
    )
    assert len(issues) == 1
    assert issues[0].category == "orphan_hook"
    assert issues[0].path == orphan


def test_orphan_hook_skipped_when_no_hooks_dir(tmp_path: Path) -> None:
    # No hooks/ subdirectory under the scan root: nothing to flag.
    assert _check_orphan_hooks(_result(root=tmp_path)) == []


def test_orphan_hook_inline_command_means_all_files_orphan(tmp_path: Path) -> None:
    # A hook with no referenced_script (inline shell) means every file
    # in hooks/ is unreferenced from that hook's perspective.
    root = tmp_path
    hooks_dir = root / "hooks"
    hooks_dir.mkdir()
    f = hooks_dir / "a.sh"
    f.write_text("#!/bin/sh\n")
    issues = _check_orphan_hooks(_result(root=root, hooks=(_hook(None),)))
    assert len(issues) == 1
    assert issues[0].path == f


# --- _check_conflicting_keybindings -----------------------------------


def _kb(entries: tuple[KeybindingEntry, ...]) -> KeybindingsBundle:
    return KeybindingsBundle(path=Path("/r/keybindings.json"), entries=entries)


def test_conflicting_keybinding_flagged() -> None:
    entries = (
        KeybindingEntry(context="global", key="ctrl+x", action="quit"),
        KeybindingEntry(context="global", key="ctrl+x", action="suspend"),
    )
    issues = _check_conflicting_keybindings(_result(keybindings=_kb(entries)))
    assert len(issues) == 1
    assert issues[0].category == "conflicting_binding"
    assert "ctrl+x" in issues[0].reason


def test_duplicate_keybinding_with_same_action_is_silent() -> None:
    # Same context, same key, same action — not a conflict.
    entries = (
        KeybindingEntry(context="global", key="ctrl+x", action="quit"),
        KeybindingEntry(context="global", key="ctrl+x", action="quit"),
    )
    assert _check_conflicting_keybindings(_result(keybindings=_kb(entries))) == []


def test_same_key_different_contexts_not_a_conflict() -> None:
    entries = (
        KeybindingEntry(context="editor", key="ctrl+x", action="cut"),
        KeybindingEntry(context="global", key="ctrl+x", action="quit"),
    )
    assert _check_conflicting_keybindings(_result(keybindings=_kb(entries))) == []


def test_no_keybindings_returns_empty() -> None:
    assert _check_conflicting_keybindings(_result()) == []
