from collections.abc import Iterable
from typing import Literal, NamedTuple, cast

from rich.console import RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.widget import Widget
from textual.widgets import DataTable, Markdown, Rule, Static

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


Severity = Literal["error", "warning", "info"]

# Rich style colors used in Text() spans. Rich style strings do not
# substitute Textual theme variables, so we use plain Rich color names
# here; widget-level theming happens via CSS classes in app.tcss.
COLOR_MUTED = "bright_black"
COLOR_PRIMARY = "cyan"
COLOR_ACCENT = "magenta"
COLOR_SUCCESS = "green"
COLOR_ERROR = "red"
COLOR_WARNING = "yellow"
COLOR_INFO = "blue"

_SEVERITY_BY_CATEGORY: dict[str, Severity] = {
    "plugin_state": "error",
    "source": "error",
    "orphan_hook": "warning",
    "commands": "warning",
    "hooks": "warning",
    "memory": "warning",
    "mcp": "warning",
    "scope_override_command": "info",
    "scope_override_plugin": "info",
    "scope_layered_memory": "info",
    "conflicting_binding": "info",
}

_SEVERITY_COLOR: dict[Severity, str] = {
    "error": COLOR_ERROR,
    "warning": COLOR_WARNING,
    "info": COLOR_INFO,
}


def warning_severity(category: str) -> Severity:
    return _SEVERITY_BY_CATEGORY.get(category, "warning")


def redact(value: str) -> str:
    """Mask the middle of a potentially-secret string.

    Short values (under 8 chars) pass through unchanged so a flag like
    "1" or "true" remains readable. Longer values keep the first 4 and
    last 2 characters with an ellipsis in between.
    """
    if len(value) < 8:
        return value
    return value[:4] + "…" + value[-2:]


_BADGE_COLOR = {
    "success": COLOR_SUCCESS,
    "error": COLOR_ERROR,
    "warning": COLOR_WARNING,
    "info": COLOR_INFO,
    "muted": COLOR_MUTED,
}


def _badge(
    label: str, kind: Literal["success", "error", "warning", "info", "muted"]
) -> Text:
    return Text(label, style=f"bold {_BADGE_COLOR[kind]}")


def _kv_table(rows: Iterable[tuple[str, RenderableType]]) -> Table:
    """Two-column key/value grid used for metadata blocks."""
    table = Table.grid(padding=(0, 2), expand=False)
    table.add_column(style=f"bold {COLOR_MUTED}")
    table.add_column(overflow="fold")
    for key, value in rows:
        table.add_row(key, value)
    return table


class _PendingDataTable(DataTable[str]):
    """A DataTable whose columns and rows are buffered at construction
    time and added on mount.

    Plain `DataTable.add_columns` calls `self.app.console` for width
    measurement, which raises `NoActiveAppError` if the widget is built
    outside an active app context — and our renderers run before mount.
    """

    def __init__(
        self, columns: tuple[str, ...], rows: tuple[tuple[str, ...], ...]
    ) -> None:
        super().__init__(zebra_stripes=True, cursor_type="row")
        self._pending_columns = columns
        self._pending_rows = rows

    def on_mount(self) -> None:
        self.add_columns(*self._pending_columns)
        for row in self._pending_rows:
            self.add_row(*row)


def _section(title: str) -> list[Widget]:
    """A section header — a styled title above a horizontal Rule.

    Textual's Rule widget doesn't support inline titles, so this pairs
    a styled Static with a plain Rule. Renderers append the result via
    `.extend(...)` to keep call sites compact.
    """
    return [
        Static(Text(title, style=f"bold {COLOR_PRIMARY}"), classes="section-title"),
        Rule(),
    ]


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


def sidebar_label(report: ScanReport, name: str, key: str) -> Text:
    """Build a Rich Text label for the sidebar with colored counts.

    Returns rich Text (not str) so brackets and counts can carry style
    spans — zero counts dim, non-zero counts default-styled, warnings row
    with non-zero count rendered in warning color.
    """
    user_n = category_count(report.user, key) if report.user else 0
    project_n = category_count(report.project, key) if report.project else 0
    text = Text(f"{name}  ")
    if report.user is not None and report.project is not None:
        text.append("U:", style=COLOR_MUTED)
        text.append(str(user_n), style=_count_style(key, user_n))
        text.append(" P:", style=COLOR_MUTED)
        text.append(str(project_n), style=_count_style(key, project_n))
    else:
        total = user_n + project_n
        text.append(f"({total})", style=_count_style(key, total))
    return text


def _count_style(key: str, n: int) -> str:
    if n == 0:
        return COLOR_MUTED
    if key == "warnings":
        return f"bold {COLOR_WARNING}"
    return ""


def items_for_report(
    report: ScanReport, key: str
) -> list[tuple[Text, object, str]]:
    """Items with rich-text labels.

    In multi-scope mode labels are prefixed with a styled `[U]` or `[P]`
    marker; in single-scope mode the bare label is used.
    """
    multi = report.user is not None and report.project is not None
    items: list[tuple[Text, object, str]] = []
    if report.user is not None:
        for label, payload in category_items(report.user, key):
            display = _prefix(label, "U", COLOR_PRIMARY) if multi else label
            items.append((display, payload, "user"))
    if report.project is not None:
        for label, payload in category_items(report.project, key):
            display = _prefix(label, "P", COLOR_ACCENT) if multi else label
            items.append((display, payload, "project"))
    return items


def _prefix(label: Text, marker: str, color: str) -> Text:
    out = Text()
    out.append(f"[{marker}] ", style=f"bold {color}")
    out.append_text(label)
    return out


def category_items(  # noqa: PLR0911
    result: ScanResult, key: str
) -> list[tuple[Text, object]]:
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


def render_detail_widgets(
    key: str, payload: object, scope: str = ""
) -> list[Widget]:
    """Build widgets to mount into the detail pane for a single item.

    The caller (MainScreen._refresh_detail) is responsible for mounting
    the returned widgets into the detail container.
    """
    widgets: list[Widget] = []
    if scope:
        widgets.append(
            Static(
                Text(f"[{scope}]", style=f"bold {COLOR_PRIMARY}"),
                classes="scope-tag",
            )
        )
    widgets.extend(_render_body_widgets(key, payload))
    return widgets


def _render_body_widgets(key: str, payload: object) -> list[Widget]:  # noqa: PLR0911
    match key:
        case "settings":
            return _settings_detail_widgets(payload)
        case "hooks":
            return _hooks_detail_widgets(payload)
        case "commands":
            return _commands_detail_widgets(payload)
        case "plugins":
            return _plugins_detail_widgets(payload)
        case "memory":
            return _memory_detail_widgets(payload)
        case "keybindings":
            return _keybindings_detail_widgets(payload)
        case "mcp":
            return _mcp_detail_widgets(payload)
        case "warnings":
            return _warnings_detail_widgets(payload)
        case _:
            return [Static("(no renderer)")]


# --- Settings -----------------------------------------------------------


def _settings_items(s: SettingsBundle | None) -> list[tuple[Text, object]]:
    if s is None:
        return []
    return [
        (_scalar_label("Model", s.model), _SettingsItem("Model", "scalar", s.model)),
        (_scalar_label("Theme", s.theme), _SettingsItem("Theme", "scalar", s.theme)),
        (
            _scalar_label("Editor mode", s.editor_mode),
            _SettingsItem("Editor mode", "scalar", s.editor_mode),
        ),
        (
            _scalar_label("Effort level", s.effort_level),
            _SettingsItem("Effort level", "scalar", s.effort_level),
        ),
        (
            _count_label("Env vars", len(s.env)),
            _SettingsItem("Env vars", "dict", dict(s.env)),
        ),
        (
            _count_label("Permissions allow", len(s.permissions_allow)),
            _SettingsItem("Permissions allow", "list", list(s.permissions_allow)),
        ),
        (
            _count_label("Permissions deny", len(s.permissions_deny)),
            _SettingsItem("Permissions deny", "list", list(s.permissions_deny)),
        ),
        (
            _count_label("Permissions ask", len(s.permissions_ask)),
            _SettingsItem("Permissions ask", "list", list(s.permissions_ask)),
        ),
        (
            _count_label("Enabled plugins", len(s.enabled_plugins)),
            _SettingsItem("Enabled plugins", "list", list(s.enabled_plugins)),
        ),
        (
            _count_label("Hooks dir files", s.hooks_dir_files),
            _SettingsItem("Hooks dir files", "scalar", str(s.hooks_dir_files)),
        ),
    ]


def _scalar_label(name: str, value: object) -> Text:
    text = Text(f"{name}: ")
    if value:
        text.append(str(value))
    else:
        text.append("—", style=COLOR_MUTED)
    return text


def _count_label(name: str, count: int) -> Text:
    text = Text(f"{name}: ")
    text.append(str(count), style=COLOR_MUTED if count == 0 else "")
    return text


def _settings_detail_widgets(payload: object) -> list[Widget]:
    if not isinstance(payload, _SettingsItem):
        return [Static("(no setting selected)", classes="muted")]
    header = Static(
        Text(payload.label, style=f"bold {COLOR_PRIMARY}"), classes="detail-header"
    )
    return [header, _settings_body(payload)]


def _settings_body(payload: _SettingsItem) -> Widget:
    if payload.kind == "scalar":
        if payload.value:
            return Static(str(payload.value))
        return Static("(unset)", classes="muted")
    if payload.kind == "dict" and isinstance(payload.value, dict):
        return _dict_body(cast("dict[str, object]", payload.value))  # pyright: ignore[reportUnknownMemberType]
    if payload.kind == "list" and isinstance(payload.value, list):
        return _list_body(cast("list[object]", payload.value))  # pyright: ignore[reportUnknownMemberType]
    return Static("(unsupported)")


def _dict_body(d: dict[str, object]) -> Widget:
    if not d:
        return Static("(empty)", classes="muted")
    t = Table.grid(padding=(0, 2))
    t.add_column(style="bold")
    t.add_column(overflow="fold")
    for k in sorted(d.keys()):
        t.add_row(k, str(d[k]))
    return Static(t)


def _list_body(lst: list[object]) -> Widget:
    if not lst:
        return Static("(empty)", classes="muted")
    t = Table.grid(padding=(0, 2))
    t.add_column(style=COLOR_MUTED, justify="right")
    t.add_column(overflow="fold")
    for i, v in enumerate(lst, 1):
        t.add_row(str(i), str(v))
    return Static(t)


# --- Hooks --------------------------------------------------------------


def _hooks_items(hooks: tuple[HookSpec, ...]) -> list[tuple[Text, object]]:
    items: list[tuple[Text, object]] = []
    for h in hooks:
        text = Text()
        text.append(h.event, style="bold")
        text.append(f"  [{h.matcher or '*'}]  ", style=COLOR_MUTED)
        preview = h.command[:40] + ("…" if len(h.command) > 40 else "")
        text.append(preview)
        items.append((text, h))
    return items


def _hooks_detail_widgets(payload: object) -> list[Widget]:
    if not isinstance(payload, HookSpec):
        return [Static("(no hook selected)", classes="muted")]
    rows: list[tuple[str, RenderableType]] = [
        ("Event", Text(payload.event, style="bold")),
        (
            "Matcher",
            payload.matcher or Text("(none — runs on all)", style=COLOR_MUTED),
        ),
        ("Type", payload.type),
        (
            "Timeout",
            str(payload.timeout)
            if payload.timeout is not None
            else Text("(default)", style=COLOR_MUTED),
        ),
    ]
    widgets: list[Widget] = [Static(_kv_table(rows))]
    widgets.extend(_section("Command"))
    widgets.append(
        Static(
            Syntax(
                payload.command,
                "bash",
                theme="ansi_dark",
                word_wrap=True,
                background_color="default",
            )
        )
    )
    if payload.referenced_script is not None:
        badge = (
            _badge("exists", "success")
            if payload.script_exists
            else _badge("MISSING", "error")
        )
        script_line = Text("Script: ")
        script_line.append_text(badge)
        script_line.append("  ")
        script_line.append(str(payload.referenced_script), style=COLOR_MUTED)
        widgets.append(Static(script_line))
    return widgets


# --- Slash commands -----------------------------------------------------


def _commands_items(commands: tuple[SlashCommand, ...]) -> list[tuple[Text, object]]:
    items: list[tuple[Text, object]] = []
    for c in commands:
        text = Text()
        text.append(f"/{c.name}", style="bold")
        if c.description:
            text.append(" — ", style=COLOR_MUTED)
            text.append(c.description)
        items.append((text, c))
    return items


def _commands_detail_widgets(payload: object) -> list[Widget]:
    if not isinstance(payload, SlashCommand):
        return [Static("(no command selected)", classes="muted")]
    header_text = Text()
    header_text.append(f"/{payload.name}", style=f"bold {COLOR_PRIMARY}")
    if payload.description:
        header_text.append("  ")
        header_text.append(payload.description)
    widgets: list[Widget] = [Static(header_text, classes="detail-header")]
    tools = ", ".join(payload.allowed_tools) if payload.allowed_tools else "(none)"
    rows: list[tuple[str, RenderableType]] = [
        ("Path", str(payload.path)),
        (
            "Argument hint",
            payload.argument_hint or Text("(none)", style=COLOR_MUTED),
        ),
        ("Allowed tools", tools),
    ]
    widgets.append(Static(_kv_table(rows)))
    widgets.extend(_section("Body"))
    widgets.append(Markdown(payload.body or "_(empty)_"))
    return widgets


# --- Plugins ------------------------------------------------------------


def _plugins_items(plugins: tuple[Plugin, ...]) -> list[tuple[Text, object]]:
    items: list[tuple[Text, object]] = []
    for p in plugins:
        text = Text()
        text.append(p.qualified_id, style="bold")
        text.append("  ")
        if p.enabled:
            text.append("enabled", style=COLOR_SUCCESS)
        else:
            text.append("disabled", style=COLOR_MUTED)
        text.append(f"  [{len(p.installations)} install(s)]", style=COLOR_MUTED)
        items.append((text, p))
    return items


def _plugins_detail_widgets(payload: object) -> list[Widget]:
    if not isinstance(payload, Plugin):
        return [Static("(no plugin selected)", classes="muted")]
    header_text = Text()
    header_text.append(payload.qualified_id, style=f"bold {COLOR_PRIMARY}")
    header_text.append("  ")
    header_text.append_text(
        _badge("enabled", "success") if payload.enabled else _badge("disabled", "muted")
    )
    widgets: list[Widget] = [Static(header_text, classes="detail-header")]
    rows: list[tuple[str, RenderableType]] = [
        ("ID", payload.id),
        ("Marketplace", payload.marketplace or Text("(none)", style=COLOR_MUTED)),
    ]
    widgets.append(Static(_kv_table(rows)))
    widgets.extend(_section(f"Installations ({len(payload.installations)})"))
    if not payload.installations:
        widgets.append(Static("(no installations on disk)", classes="muted"))
    else:
        install_rows: tuple[tuple[str, ...], ...] = tuple(
            (
                str(i),
                inst.scope,
                inst.version,
                inst.git_commit_sha[:12] if inst.git_commit_sha else "—",
                str(inst.install_path),
                str(inst.project_path) if inst.project_path else "—",
            )
            for i, inst in enumerate(payload.installations, 1)
        )
        widgets.append(
            _PendingDataTable(
                columns=("#", "scope", "version", "git", "path", "project"),
                rows=install_rows,
            )
        )
    return widgets


# --- Memory -------------------------------------------------------------

_MEMORY_KIND_LABEL = {
    "claude_md": "CLAUDE",
    "memory_index": "index",
    "memory_entry": "entry",
}


def _memory_items(memory: tuple[MemoryFile, ...]) -> list[tuple[Text, object]]:
    items: list[tuple[Text, object]] = []
    for m in memory:
        kind = _MEMORY_KIND_LABEL.get(m.kind, m.kind)
        text = Text()
        text.append(kind, style="bold")
        text.append("  ")
        text.append(m.path.name)
        if m.project_label:
            text.append(f"  @ {m.project_label}", style=COLOR_MUTED)
        text.append(f"  ({len(m.body)} chars", style=COLOR_MUTED)
        if m.has_frontmatter:
            text.append(" +fm", style=COLOR_INFO)
        text.append(")", style=COLOR_MUTED)
        items.append((text, m))
    return items


def _memory_detail_widgets(payload: object) -> list[Widget]:
    if not isinstance(payload, MemoryFile):
        return [Static("(no memory file selected)", classes="muted")]
    kind_label = _MEMORY_KIND_LABEL.get(payload.kind, payload.kind)
    header_text = Text()
    header_text.append(kind_label, style=f"bold {COLOR_PRIMARY}")
    header_text.append("  ")
    header_text.append(payload.path.name)
    if payload.has_frontmatter:
        header_text.append("  ")
        header_text.append_text(_badge("+fm", "info"))
    widgets: list[Widget] = [Static(header_text, classes="detail-header")]
    rows: list[tuple[str, RenderableType]] = [
        (
            "Project",
            payload.project_label or Text("(user-level)", style=COLOR_MUTED),
        ),
        ("Path", str(payload.path)),
        ("Size", f"{len(payload.body)} chars"),
    ]
    widgets.append(Static(_kv_table(rows)))
    widgets.extend(_section("Body"))
    widgets.append(Markdown(payload.body or "_(empty)_"))
    return widgets


# --- Keybindings --------------------------------------------------------


def _keybindings_items(
    kb: KeybindingsBundle | None,
) -> list[tuple[Text, object]]:
    if kb is None:
        return []
    items: list[tuple[Text, object]] = []
    for e in kb.entries:
        text = Text()
        text.append(f"[{e.context}] ", style=COLOR_MUTED)
        text.append(e.key, style="bold")
        text.append(" → ", style=COLOR_MUTED)
        text.append(e.action)
        items.append((text, e))
    return items


def _keybindings_detail_widgets(payload: object) -> list[Widget]:
    if not isinstance(payload, KeybindingEntry):
        return [Static("(no binding selected)", classes="muted")]
    rows: list[tuple[str, RenderableType]] = [
        ("Context", payload.context),
        ("Key", Text(payload.key, style="bold")),
        ("Action", payload.action),
    ]
    return [Static(_kv_table(rows))]


# --- MCP servers --------------------------------------------------------


def _mcp_items(mcp: tuple[MCPServer, ...]) -> list[tuple[Text, object]]:
    items: list[tuple[Text, object]] = []
    for m in mcp:
        text = Text()
        text.append(m.name, style="bold")
        text.append(f"  ({m.command or '?'})", style=COLOR_MUTED)
        items.append((text, m))
    return items


def _mcp_detail_widgets(payload: object) -> list[Widget]:
    if not isinstance(payload, MCPServer):
        return [Static("(no MCP server selected)", classes="muted")]
    header_text = Text(payload.name, style=f"bold {COLOR_PRIMARY}")
    widgets: list[Widget] = [Static(header_text, classes="detail-header")]
    args = " ".join(payload.args) if payload.args else "(none)"
    rows: list[tuple[str, RenderableType]] = [
        ("Source", str(payload.source_path)),
        ("Command", payload.command or Text("(none)", style=COLOR_MUTED)),
        ("Args", args),
    ]
    widgets.append(Static(_kv_table(rows)))
    widgets.extend(_section(f"Environment ({len(payload.env)})"))
    if not payload.env:
        widgets.append(Static("(no env vars)", classes="muted"))
    else:
        env_rows: tuple[tuple[str, ...], ...] = tuple(
            (k, redact(payload.env[k])) for k in sorted(payload.env.keys())
        )
        widgets.append(
            _PendingDataTable(columns=("Key", "Value (redacted)"), rows=env_rows)
        )
    return widgets


# --- Scan warnings ------------------------------------------------------


def _warnings_items(
    warnings: tuple[ScanWarning, ...],
) -> list[tuple[Text, object]]:
    items: list[tuple[Text, object]] = []
    for w in warnings:
        sev = warning_severity(w.category)
        color = _SEVERITY_COLOR[sev]
        text = Text()
        text.append(f"[{w.category}] ", style=f"bold {color}")
        preview = w.reason[:60] + ("…" if len(w.reason) > 60 else "")
        text.append(preview)
        items.append((text, w))
    return items


def _warnings_detail_widgets(payload: object) -> list[Widget]:
    if not isinstance(payload, ScanWarning):
        return [Static("(no warning selected)", classes="muted")]
    sev = warning_severity(payload.category)
    color = _SEVERITY_COLOR[sev]
    header_text = Text(payload.category, style=f"bold {color}")
    widgets: list[Widget] = [Static(header_text, classes="detail-header")]
    if payload.path:
        widgets.append(Static(Text(str(payload.path), style=COLOR_MUTED)))
    panel = Panel(Text(payload.reason), border_style=color, padding=(0, 1))
    widgets.append(Static(panel))
    return widgets
