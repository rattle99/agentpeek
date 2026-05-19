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
