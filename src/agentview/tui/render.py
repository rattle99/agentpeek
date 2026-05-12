from typing import Literal, NamedTuple, cast

from rich.console import RenderableType
from rich.text import Text

from agentview.models import (
    HookSpec,
    KeybindingEntry,
    KeybindingsBundle,
    MCPServer,
    MemoryFile,
    Plugin,
    ScanReport,
    ScanResult,
    ScanWarning,
    SettingsBundle,
    SlashCommand,
)

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("settings", "Settings"),
    ("hooks", "Hooks"),
    ("commands", "Slash commands"),
    ("plugins", "Plugins"),
    ("memory", "Memory"),
    ("keybindings", "Keybindings"),
    ("mcp", "MCP servers"),
    ("warnings", "Scan warnings"),
)


class _SettingsItem(NamedTuple):
    label: str
    kind: Literal["scalar", "dict", "list"]
    value: object


def category_count(result: ScanResult, key: str) -> int:
    counts = {
        "settings": 1 if result.settings else 0,
        "hooks": len(result.hooks),
        "commands": len(result.commands),
        "plugins": len(result.plugins),
        "memory": len(result.memory),
        "keybindings": (len(result.keybindings.entries) if result.keybindings else 0),
        "mcp": len(result.mcp),
        "warnings": len(result.warnings),
    }
    return counts.get(key, 0)


def sidebar_label(report: ScanReport, name: str, key: str) -> str:
    user_n = category_count(report.user, key) if report.user else 0
    project_n = category_count(report.project, key) if report.project else 0
    if report.user is not None and report.project is not None:
        return f"{name}  U:{user_n} P:{project_n}"
    return f"{name}  ({user_n + project_n})"


def items_for_report(report: ScanReport, key: str) -> list[tuple[str, object, str]]:
    multi = report.user is not None and report.project is not None
    items: list[tuple[str, object, str]] = []
    if report.user is not None:
        for label, payload in category_items(report.user, key):
            display = f"[U] {label}" if multi else label
            items.append((display, payload, "user"))
    if report.project is not None:
        for label, payload in category_items(report.project, key):
            display = f"[P] {label}" if multi else label
            items.append((display, payload, "project"))
    return items


def category_items(  # noqa: PLR0911
    result: ScanResult, key: str
) -> list[tuple[str, object]]:
    # Inherent fan-out — one branch per category. A dict-of-callables here
    # would only obscure the dispatch.
    match key:
        case "settings":
            return _settings_items(result.settings)
        case "hooks":
            return _hooks_items(result.hooks)
        case "commands":
            return _commands_items(result.commands)
        case "plugins":
            return _plugins_items(result.plugins)
        case "memory":
            return _memory_items(result.memory)
        case "keybindings":
            return _keybindings_items(result.keybindings)
        case "mcp":
            return _mcp_items(result.mcp)
        case "warnings":
            return _warnings_items(result.warnings)
        case _:
            return []


def render_detail(key: str, payload: object, scope: str = "") -> RenderableType:
    body = _render_detail_body(key, payload)
    if scope and isinstance(body, Text):
        header = Text(f"[{scope}]\n", style="bold")
        return header + body
    return body


def _render_detail_body(key: str, payload: object) -> RenderableType:  # noqa: PLR0911
    match key:
        case "settings":
            return _settings_detail(payload)
        case "hooks":
            return _hooks_detail(payload)
        case "commands":
            return _commands_detail(payload)
        case "plugins":
            return _plugins_detail(payload)
        case "memory":
            return _memory_detail(payload)
        case "keybindings":
            return _keybindings_detail(payload)
        case "mcp":
            return _mcp_detail(payload)
        case "warnings":
            return _warnings_detail(payload)
        case _:
            return Text("(no renderer)")


def _settings_items(s: SettingsBundle | None) -> list[tuple[str, object]]:
    if s is None:
        return []
    return [
        (f"Model: {s.model or '—'}", _SettingsItem("Model", "scalar", s.model)),
        (f"Theme: {s.theme or '—'}", _SettingsItem("Theme", "scalar", s.theme)),
        (
            f"Editor mode: {s.editor_mode or '—'}",
            _SettingsItem("Editor mode", "scalar", s.editor_mode),
        ),
        (
            f"Effort level: {s.effort_level or '—'}",
            _SettingsItem("Effort level", "scalar", s.effort_level),
        ),
        (
            f"Env vars: {len(s.env)}",
            _SettingsItem("Env vars", "dict", dict(s.env)),
        ),
        (
            f"Permissions allow: {len(s.permissions_allow)}",
            _SettingsItem("Permissions allow", "list", list(s.permissions_allow)),
        ),
        (
            f"Permissions deny: {len(s.permissions_deny)}",
            _SettingsItem("Permissions deny", "list", list(s.permissions_deny)),
        ),
        (
            f"Permissions ask: {len(s.permissions_ask)}",
            _SettingsItem("Permissions ask", "list", list(s.permissions_ask)),
        ),
        (
            f"Enabled plugins: {len(s.enabled_plugins)}",
            _SettingsItem("Enabled plugins", "list", list(s.enabled_plugins)),
        ),
        (
            f"Hooks dir files: {s.hooks_dir_files}",
            _SettingsItem("Hooks dir files", "scalar", str(s.hooks_dir_files)),
        ),
    ]


def _settings_detail(payload: object) -> RenderableType:
    if not isinstance(payload, _SettingsItem):
        return Text("(no setting selected)")
    label = payload.label
    if payload.kind == "scalar":
        text = f"{label}: {payload.value or '(unset)'}"
    elif payload.kind == "dict" and isinstance(payload.value, dict):
        d = cast("dict[str, object]", payload.value)  # pyright: ignore[reportUnknownMemberType]
        text = (
            f"{label}: (empty)"
            if not d
            else f"{label}:\n" + "\n".join(f"  {k} = {d[k]}" for k in sorted(d.keys()))
        )
    elif payload.kind == "list" and isinstance(payload.value, list):
        lst = cast("list[object]", payload.value)  # pyright: ignore[reportUnknownMemberType]
        text = (
            f"{label}: (empty)"
            if not lst
            else f"{label}:\n" + "\n".join(f"  - {v}" for v in lst)
        )
    else:
        text = f"{label}: (unsupported)"
    return Text(text)


def _hooks_items(hooks: tuple[HookSpec, ...]) -> list[tuple[str, object]]:
    return [
        (
            f"{h.event} [{h.matcher or '*'}]: "
            f"{h.command[:40]}{'…' if len(h.command) > 40 else ''}",
            h,
        )
        for h in hooks
    ]


def _hooks_detail(payload: object) -> RenderableType:
    if not isinstance(payload, HookSpec):
        return Text("(no hook selected)")
    lines = [
        f"Event:           {payload.event}",
        f"Matcher:         {payload.matcher or '(none — runs on all)'}",
        f"Type:            {payload.type}",
        f"Command:         {payload.command}",
        f"Timeout:         "
        f"{payload.timeout if payload.timeout is not None else '(default)'}",
    ]
    if payload.referenced_script is not None:
        status = "exists" if payload.script_exists else "MISSING"
        lines.append(f"Script path:     {payload.referenced_script}")
        lines.append(f"Script status:   {status}")
    return Text("\n".join(lines))


def _commands_items(
    commands: tuple[SlashCommand, ...],
) -> list[tuple[str, object]]:
    return [
        (
            f"/{c.name}{(' — ' + c.description) if c.description else ''}",
            c,
        )
        for c in commands
    ]


def _commands_detail(payload: object) -> RenderableType:
    if not isinstance(payload, SlashCommand):
        return Text("(no command selected)")
    tools = ", ".join(payload.allowed_tools) if payload.allowed_tools else "(none)"
    lines = [
        f"Name:            /{payload.name}",
        f"Path:            {payload.path}",
        f"Description:     {payload.description or '(none)'}",
        f"Argument hint:   {payload.argument_hint or '(none)'}",
        f"Allowed tools:   {tools}",
        "",
        "── Body ──",
        payload.body or "(empty)",
    ]
    return Text("\n".join(lines))


def _plugins_items(plugins: tuple[Plugin, ...]) -> list[tuple[str, object]]:
    return [
        (
            f"{p.qualified_id} "
            f"{'(enabled)' if p.enabled else '(disabled)'} "
            f"[{len(p.installations)} install(s)]",
            p,
        )
        for p in plugins
    ]


def _plugins_detail(payload: object) -> RenderableType:
    if not isinstance(payload, Plugin):
        return Text("(no plugin selected)")
    lines = [
        f"ID:              {payload.id}",
        f"Marketplace:     {payload.marketplace or '(none)'}",
        f"Qualified ID:    {payload.qualified_id}",
        f"Enabled:         {'yes' if payload.enabled else 'no'}",
        f"Installations:   {len(payload.installations)}",
    ]
    for i, inst in enumerate(payload.installations, 1):
        lines.append("")
        lines.append(f"  [{i}] scope={inst.scope}, version={inst.version}")
        lines.append(f"      path={inst.install_path}")
        if inst.git_commit_sha:
            lines.append(f"      git={inst.git_commit_sha[:12]}")
        if inst.project_path:
            lines.append(f"      project={inst.project_path}")
    return Text("\n".join(lines))


_MEMORY_KIND_LABEL = {
    "claude_md": "CLAUDE",
    "memory_index": "index",
    "memory_entry": "entry",
}


def _memory_items(memory: tuple[MemoryFile, ...]) -> list[tuple[str, object]]:
    items: list[tuple[str, object]] = []
    for m in memory:
        kind = _MEMORY_KIND_LABEL.get(m.kind, m.kind)
        scope = f" @ {m.project_label}" if m.project_label else ""
        fm = " +fm" if m.has_frontmatter else ""
        items.append((f"{kind}  {m.path.name}{scope}  ({len(m.body)} chars{fm})", m))
    return items


def _memory_detail(payload: object) -> RenderableType:
    if not isinstance(payload, MemoryFile):
        return Text("(no memory file selected)")
    lines = [
        f"Kind:            {payload.kind}",
        f"Project:         {payload.project_label or '(user-level)'}",
        f"Path:            {payload.path}",
        f"Size:            {len(payload.body)} chars",
        f"Has frontmatter: {'yes' if payload.has_frontmatter else 'no'}",
        "",
        "── Body ──",
        payload.body or "(empty)",
    ]
    return Text("\n".join(lines))


def _keybindings_items(
    kb: KeybindingsBundle | None,
) -> list[tuple[str, object]]:
    if kb is None:
        return []
    return [(f"[{e.context}] {e.key} → {e.action}", e) for e in kb.entries]


def _keybindings_detail(payload: object) -> RenderableType:
    if not isinstance(payload, KeybindingEntry):
        return Text("(no binding selected)")
    return Text(
        "\n".join(
            [
                f"Context:  {payload.context}",
                f"Key:      {payload.key}",
                f"Action:   {payload.action}",
            ]
        )
    )


def _mcp_items(mcp: tuple[MCPServer, ...]) -> list[tuple[str, object]]:
    return [(f"{m.name}  ({m.command or '?'})", m) for m in mcp]


def _mcp_detail(payload: object) -> RenderableType:
    if not isinstance(payload, MCPServer):
        return Text("(no MCP server selected)")
    args = " ".join(payload.args) if payload.args else "(none)"
    lines = [
        f"Name:     {payload.name}",
        f"Source:   {payload.source_path}",
        f"Command:  {payload.command or '(none)'}",
        f"Args:     {args}",
        f"Env:      {len(payload.env)} variable(s)",
    ]
    if payload.env:
        for k in sorted(payload.env.keys()):
            # Env values can be secrets — show only a redacted preview.
            v = payload.env[k]
            shown = v if len(v) < 8 else v[:4] + "…" + v[-2:]
            lines.append(f"  {k}={shown}")
    return Text("\n".join(lines))


def _warnings_items(
    warnings: tuple[ScanWarning, ...],
) -> list[tuple[str, object]]:
    return [
        (
            f"[{w.category}] {w.reason[:60]}{'…' if len(w.reason) > 60 else ''}",
            w,
        )
        for w in warnings
    ]


def _warnings_detail(payload: object) -> RenderableType:
    if not isinstance(payload, ScanWarning):
        return Text("(no warning selected)")
    lines = [
        f"Category:  {payload.category}",
        f"Path:      {payload.path or '(none)'}",
        "",
        f"Reason:    {payload.reason}",
    ]
    return Text("\n".join(lines))
