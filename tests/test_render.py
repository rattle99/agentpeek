import dataclasses
from pathlib import Path

from agentpeek.models import (
    HookSpec,
    KeybindingEntry,
    KeybindingsBundle,
    MCPServer,
    MemoryFile,
    Plugin,
    PluginAgent,
    PluginInstallation,
    PluginSkill,
    ScanReport,
    ScanResult,
    ScanWarning,
    SettingsBundle,
    SlashCommand,
)
from agentpeek.tui.render import (
    _BODY_PREVIEW_LIMIT,
    COLOR_MUTED,
    COLOR_WARNING,
    _bounded_markdown,
    _hooks_detail_widgets,
    _plugins_detail_widgets,
    item_body,
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


# --- item_body ---------------------------------------------------------
# `b` yanks the result of item_body. Every dispatched type and every
# None-returning branch is asserted so a regression here can't quietly
# yank the wrong content.


def test_item_body_memory() -> None:
    m = MemoryFile(
        path=Path("/r/CLAUDE.md"),
        body="# top of mind\n",
        has_frontmatter=False,
        kind="claude_md",
        project_label=None,
    )
    assert item_body(m) == "# top of mind\n"


def test_item_body_slash_command() -> None:
    c = SlashCommand(
        path=Path("/r/cmd.md"),
        name="cmd",
        description=None,
        argument_hint=None,
        allowed_tools=(),
        body="do the thing",
    )
    assert item_body(c) == "do the thing"


def test_item_body_plugin_skill() -> None:
    s = PluginSkill(
        path=Path("/r/SKILL.md"),
        name="brainstorm",
        description="think out loud",
        body="## steps\n1. open mind",
    )
    assert item_body(s) == "## steps\n1. open mind"


def test_item_body_plugin_agent() -> None:
    a = PluginAgent(
        path=Path("/r/AGENT.md"),
        name="reviewer",
        description="code review",
        body="You are a senior reviewer.",
    )
    assert item_body(a) == "You are a senior reviewer."


def test_item_body_hook_script_form() -> None:
    h = HookSpec(
        event="PreToolUse",
        matcher=None,
        type="command",
        command="bash ~/.claude/hooks/foo.sh",
        timeout=None,
        referenced_script=Path("/r/hooks/foo.sh"),
        script_exists=True,
    )
    assert item_body(h) == "bash ~/.claude/hooks/foo.sh"


def test_item_body_hook_inline_form() -> None:
    h = HookSpec(
        event="PreToolUse",
        matcher=None,
        type="command",
        command="echo hello",
        timeout=None,
        referenced_script=None,
        script_exists=False,
    )
    assert item_body(h) == "echo hello"


def test_item_body_settings_returns_none() -> None:
    bundle = SettingsBundle(
        user_settings_path=None,
        local_settings_path=None,
        model=None,
        theme=None,
        editor_mode=None,
        effort_level=None,
        output_style=None,
        status_line=None,
        skip_auto_permission_prompt=False,
        env={},
        permissions_allow=(),
        permissions_deny=(),
        permissions_ask=(),
        enabled_plugins=(),
        policy_restrictions={},
        hooks_raw={},
        hooks_dir_files=0,
    )
    # Settings bundle has no single "body" — yanking it is a no-op.
    assert item_body(bundle) is None


def test_item_body_plugin_returns_none() -> None:
    p = Plugin(
        id="alpha",
        marketplace="m",
        qualified_id="alpha@m",
        enabled=True,
        installations=(),
    )
    assert item_body(p) is None


def test_item_body_keybinding_returns_none() -> None:
    e = KeybindingEntry(context="global", key="ctrl+x", action="quit")
    assert item_body(e) is None


def test_item_body_mcp_returns_none() -> None:
    s = MCPServer(
        name="srv",
        source_path=Path("/r/.claude.json"),
        command="srv",
        args=(),
        env={},
    )
    assert item_body(s) is None


def test_item_body_unknown_payload_returns_none() -> None:
    # Defensive — `b` on a row whose payload is unexpected (None, a
    # group-header sentinel, etc.) shouldn't blow up.
    assert item_body(None) is None
    assert item_body("string-payload") is None


# --- _bounded_markdown -------------------------------------------------


def test_bounded_markdown_passes_short_body_through() -> None:
    body = "# heading\n\ntext"
    md = _bounded_markdown(body)
    # Markdown widget exposes the source as `_markdown` (Textual private)
    # — assert that what we asked to render is what's in there, sliced
    # to its first line for stability across Textual versions.
    assert body in (getattr(md, "_initial_markdown", None) or "")


def test_bounded_markdown_truncates_long_body() -> None:
    body = "x" * (_BODY_PREVIEW_LIMIT + 1000)
    md = _bounded_markdown(body)
    rendered = getattr(md, "_initial_markdown", None) or ""
    assert "truncated" in rendered
    assert "1000 more bytes" in rendered
    # Don't render past the cap (plus the marker line).
    assert len(rendered) < _BODY_PREVIEW_LIMIT + 200


def test_bounded_markdown_empty_body_shows_placeholder() -> None:
    md = _bounded_markdown("")
    rendered = getattr(md, "_initial_markdown", None) or ""
    assert "empty" in rendered


# --- _hooks_detail_widgets ---------------------------------------------


def test_hooks_detail_widgets_renders_properties_card() -> None:
    h = HookSpec(
        event="PreToolUse",
        matcher="Bash",
        type="command",
        command="bash ~/.claude/hooks/foo.sh",
        timeout=30,
        referenced_script=Path("/r/hooks/foo.sh"),
        script_exists=True,
    )
    widgets = _hooks_detail_widgets(h)
    # Properties card + Command card + Script card.
    assert len(widgets) == 3


def test_hooks_detail_widgets_inline_hook_omits_script_card() -> None:
    h = HookSpec(
        event="PreToolUse",
        matcher=None,
        type="command",
        command="echo hi",
        timeout=None,
        referenced_script=None,
        script_exists=False,
    )
    widgets = _hooks_detail_widgets(h)
    # No referenced_script → no Script card.
    assert len(widgets) == 2


def test_hooks_detail_widgets_non_hook_payload_falls_back() -> None:
    widgets = _hooks_detail_widgets("not a hook")
    assert len(widgets) == 1  # placeholder Static


# --- _plugins_detail_widgets -------------------------------------------


def test_plugins_detail_widgets_minimal_plugin() -> None:
    p = Plugin(
        id="alpha",
        marketplace="m",
        qualified_id="alpha@m",
        enabled=True,
        installations=(),
    )
    widgets = _plugins_detail_widgets(p)
    # Header + Properties + Installations placeholder ("no installations
    # on disk"). No manifest, no skills/agents/commands/hooks/mcps.
    assert len(widgets) == 3


def test_plugins_detail_widgets_with_all_content_sections() -> None:
    from agentpeek.models import PluginAgent, PluginManifest, PluginSkill

    inst = PluginInstallation(
        scope="user",
        install_path=Path("/r/plugin"),
        version="1.0",
        installed_at="t",
        last_updated="t",
        git_commit_sha="abc123",
        project_path=None,
    )
    skill = PluginSkill(
        path=Path("/r/SKILL.md"), name="s", description="d", body="b"
    )
    agent = PluginAgent(
        path=Path("/r/AGENT.md"), name="a", description="d", body="b"
    )
    cmd = SlashCommand(
        path=Path("/r/cmd.md"), name="cmd", description=None,
        argument_hint=None, allowed_tools=(), body="",
    )
    hook = HookSpec(
        event="PreToolUse", matcher=None, type="command",
        command="echo hi", timeout=None,
        referenced_script=None, script_exists=False,
    )
    mcp = MCPServer(
        name="srv", source_path=Path("/r/.mcp.json"),
        command="srv", args=(), env={},
    )
    p = Plugin(
        id="alpha", marketplace="m", qualified_id="alpha@m", enabled=True,
        installations=(inst,),
        manifest=PluginManifest(
            description="d", version="1.0", author_name="a",
            author_email=None, homepage=None, license=None, keywords=(),
        ),
        skills=(skill,),
        agents=(agent,),
        commands=(cmd,),
        hooks=(hook,),
        mcps=(mcp,),
    )
    widgets = _plugins_detail_widgets(p)
    # Header + Properties + Manifest + Installations + Skills + Agents
    # + Commands + Hooks + MCPs = 9.
    assert len(widgets) == 9


def test_plugins_detail_widgets_non_plugin_payload_falls_back() -> None:
    widgets = _plugins_detail_widgets(None)
    assert len(widgets) == 1
