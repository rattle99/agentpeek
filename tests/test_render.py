import dataclasses
from pathlib import Path

from agentpeek.models import (
    HookSpec,
    KeybindingEntry,
    KeybindingsBundle,
    MCPServer,
    MemoryFile,
    Plugin,
    PluginInstallation,
    ScanReport,
    ScanResult,
    ScanWarning,
    SlashCommand,
)
from agentpeek.tui.render import (
    COLOR_MUTED,
    COLOR_WARNING,
    item_path,
    redact,
    sidebar_count,
    warning_severity,
)


def test_warning_severity_mapping() -> None:
    assert warning_severity("plugin_state") == "error"
    assert warning_severity("source") == "error"
    assert warning_severity("orphan_hook") == "warning"
    assert warning_severity("conflicting_binding") == "info"
    assert warning_severity("scope_override_command") == "info"
    assert warning_severity("scope_layered_memory") == "info"
    # Unknown category defaults to warning — a safe middle ground that
    # surfaces the issue without screaming.
    assert warning_severity("nonexistent_category") == "warning"


def test_redact_short_value_unchanged() -> None:
    # Under 8 chars passes through so flags like "1" or "true" stay readable.
    assert redact("") == ""
    assert redact("x") == "x"
    assert redact("seven77") == "seven77"


def test_redact_long_value_masks_middle() -> None:
    # 8+ chars masks: keep first 4 and last 2 with an ellipsis between.
    assert redact("abcdefgh") == "abcd…gh"
    assert redact("sk-1234567890xyz") == "sk-1…yz"


def _make_report_with_warnings(n: int) -> ScanReport:
    user_root = Path("/user/.claude")
    project_root = Path("/proj/.claude")
    warnings = tuple(
        ScanWarning(path=None, category="plugin_state", reason=f"r{i}")
        for i in range(n)
    )
    user = dataclasses.replace(ScanResult.empty(root=user_root), warnings=warnings)
    project = ScanResult.empty(root=project_root)
    return ScanReport(user=user, project=project, project_root=project_root)


def _styles(label: object) -> list[str]:
    # rich.text.Text — return the list of style strings on its spans
    # (sorted in order of appearance) so tests can assert on color tokens.
    return [str(span.style) for span in label.spans]  # type: ignore[attr-defined]


def test_sidebar_count_zero_counts_styled_muted() -> None:
    # Empty fixture: settings count is 0 in both scopes → muted style.
    report = _make_report_with_warnings(0)
    label = sidebar_count(report, "settings")
    # Multi-scope mode renders "U:0 P:0" — every count span should be muted.
    assert COLOR_MUTED in " ".join(_styles(label))


def test_sidebar_count_warnings_count_styled_warning() -> None:
    # Three plugin_state warnings on the user side → user count should
    # render in the warning color, project count stays muted.
    report = _make_report_with_warnings(3)
    label = sidebar_count(report, "warnings")
    styles = " ".join(_styles(label))
    assert COLOR_WARNING in styles
    assert COLOR_MUTED in styles  # project zero count still dim


def _empty_result() -> ScanResult:
    return ScanResult.empty(root=Path("/r/.claude"))


def test_item_path_memory() -> None:
    m = MemoryFile(
        path=Path("/r/CLAUDE.md"),
        body="x",
        has_frontmatter=False,
        kind="claude_md",
        project_label=None,
    )
    assert item_path(m, _empty_result()) == Path("/r/CLAUDE.md")


def test_item_path_slash_command() -> None:
    c = SlashCommand(
        path=Path("/r/cmd.md"),
        name="cmd",
        description=None,
        argument_hint=None,
        allowed_tools=(),
        body="",
    )
    assert item_path(c, _empty_result()) == Path("/r/cmd.md")


def test_item_path_hook_with_referenced_script() -> None:
    h = HookSpec(
        event="PreToolUse",
        matcher=None,
        type="command",
        command="bash s.sh",
        timeout=None,
        referenced_script=Path("/r/s.sh"),
        script_exists=True,
    )
    assert item_path(h, _empty_result()) == Path("/r/s.sh")


def test_item_path_hook_inline_command_returns_none() -> None:
    h = HookSpec(
        event="PreToolUse",
        matcher=None,
        type="command",
        command="echo inline",
        timeout=None,
        referenced_script=None,
        script_exists=False,
    )
    assert item_path(h, _empty_result()) is None


def test_item_path_plugin_first_installation() -> None:
    inst = PluginInstallation(
        scope="user",
        install_path=Path("/r/plugin"),
        version="1.0",
        installed_at="t",
        last_updated="t",
        git_commit_sha=None,
        project_path=None,
    )
    p = Plugin(
        id="alpha",
        marketplace="m",
        qualified_id="alpha@m",
        enabled=True,
        installations=(inst,),
    )
    assert item_path(p, _empty_result()) == Path("/r/plugin")


def test_item_path_mcp() -> None:
    s = MCPServer(
        name="srv",
        source_path=Path("/r/.claude.json"),
        command="srv",
        args=(),
        env={},
    )
    assert item_path(s, _empty_result()) == Path("/r/.claude.json")


def test_item_path_warning() -> None:
    w = ScanWarning(path=Path("/r/x"), category="hooks", reason="bad")
    assert item_path(w, _empty_result()) == Path("/r/x")


def test_item_path_keybinding_uses_bundle_path() -> None:
    e = KeybindingEntry(context="global", key="ctrl+x", action="quit")
    kb = KeybindingsBundle(path=Path("/r/keybindings.json"), entries=(e,))
    result = dataclasses.replace(_empty_result(), keybindings=kb)
    assert item_path(e, result) == Path("/r/keybindings.json")
