from collections.abc import Iterable
from typing import Literal, NamedTuple, cast

from rich.console import RenderableType
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.containers import Container
from textual.content import Content
from textual.widget import Widget
from textual.widgets import DataTable, Markdown, Static

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

# Theme variables used in Textual `Content` spans (rendered by Static /
# Label). Textual substitutes these at paint time, so colors follow the
# active theme (textual-dark, rose-pine, gruvbox, ...). Textual ships no
# dedicated $info variable, so "info" severity reuses $primary.
#
# Rich Table cells use a separate `dim` modifier — Rich's own console
# renders cells and does not substitute Textual variables, so the
# muted-in-table case can't be theme-aware. `dim` is a Rich modifier
# that renders semantically (terminal palette respects it).
COLOR_MUTED = "$text-muted"
COLOR_PRIMARY = "$primary"
COLOR_ACCENT = "$accent"
COLOR_SUCCESS = "$success"
COLOR_ERROR = "$error"
COLOR_WARNING = "$warning"
COLOR_INFO = "$primary"

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


_BadgeKind = Literal["success", "error", "warning", "info", "muted"]
_BADGE_COLOR: dict[_BadgeKind, str] = {
    "success": COLOR_SUCCESS,
    "error": COLOR_ERROR,
    "warning": COLOR_WARNING,
    "info": COLOR_INFO,
    "muted": COLOR_MUTED,
}


def _styled(text: str, style: str) -> Content:
    """Single-style Textual Content. Used as a Content.assemble part."""
    return Content(text).stylize(style)


def _badge(label: str, kind: _BadgeKind) -> Content:
    return _styled(label, f"bold {_BADGE_COLOR[kind]}")


def _muted_cell(text: str) -> Text:
    """Rich Text for a Table cell that should look muted.

    Table cells are rendered by Rich (not Textual), so Textual $vars do
    not substitute here. `dim` is a Rich-native modifier with consistent
    terminal-level dimming.
    """
    return Text(text, style="dim")


def _kv_table(rows: Iterable[tuple[str, RenderableType]]) -> Table:
    """Two-column key/value grid for metadata blocks.

    The key column uses Rich's `dim bold` modifiers rather than a Textual
    $var — Rich's Table renders column styles through its own console
    which doesn't substitute Textual theme variables.
    """
    table = Table.grid(padding=(0, 2), expand=False)
    table.add_column(style="bold dim")
    table.add_column(overflow="fold")
    for key, value in rows:
        table.add_row(key, value)
    return table


class _PendingDataTable(DataTable[str]):
    """A DataTable whose columns and rows are buffered at construction
    time and added on mount.

    DataTable.add_columns calls `self.app.console` for width measurement,
    which raises NoActiveAppError if the widget is built outside an app
    context — and our renderers run before mount.
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


def _card(
    title: str, *children: Widget, severity: Severity | None = None
) -> Container:
    """Wrap children in a titled bordered Container.

    Visual idiom borrowed from Posting's `.section`: rounded border at
    40% accent alpha, title rendered into the top-left of the border,
    border thickens on focus-within. Severity-tagged cards use the
    severity color instead of `$accent` and add a `.severity-<sev>`
    class so CSS can drive both the border color and the title color.
    """
    container = Container(*children, classes="section-card")
    container.border_title = title
    if severity is not None:
        container.add_class(f"severity-{severity}")
    return container


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


def sidebar_label(report: ScanReport, name: str, key: str) -> Content:
    """Build a Textual Content for the sidebar with theme-following counts.

    Zero counts render in $text-muted; non-zero counts in default text;
    warnings row with a non-zero count rendered in $warning so
    attention-needing categories stand out.
    """
    user_n = category_count(report.user, key) if report.user else 0
    project_n = category_count(report.project, key) if report.project else 0
    if report.user is not None and report.project is not None:
        return Content.assemble(
            f"{name}  ",
            ("U:", COLOR_MUTED),
            (str(user_n), _count_style(key, user_n)),
            (" P:", COLOR_MUTED),
            (str(project_n), _count_style(key, project_n)),
        )
    total = user_n + project_n
    return Content.assemble(
        f"{name}  ",
        (f"({total})", _count_style(key, total)),
    )


def _count_style(key: str, n: int) -> str:
    if n == 0:
        return COLOR_MUTED
    if key == "warnings":
        return f"bold {COLOR_WARNING}"
    return ""


def items_for_report(
    report: ScanReport, key: str
) -> list[tuple[Content, object, str]]:
    """Items with theme-aware labels.

    In multi-scope mode labels are prefixed with a styled `[U]` or `[P]`
    marker; in single-scope mode the bare label is used.
    """
    multi = report.user is not None and report.project is not None
    items: list[tuple[Content, object, str]] = []
    if report.user is not None:
        for label, payload in category_items(report.user, key):
            display = _prefix(label, "U", COLOR_PRIMARY) if multi else label
            items.append((display, payload, "user"))
    if report.project is not None:
        for label, payload in category_items(report.project, key):
            display = _prefix(label, "P", COLOR_ACCENT) if multi else label
            items.append((display, payload, "project"))
    return items


def _prefix(label: Content, marker: str, color: str) -> Content:
    return Content.assemble(
        (f"[{marker}] ", f"bold {color}"),
        label,
    )


def category_items(  # noqa: PLR0911
    result: ScanResult, key: str
) -> list[tuple[Content, object]]:
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
    """Build widgets to mount into the detail pane for one item.

    The caller (MainScreen._refresh_detail) mounts the returned widgets
    into the detail container.
    """
    widgets: list[Widget] = []
    if scope:
        widgets.append(
            Static(
                _styled(f"[{scope}]", f"bold {COLOR_PRIMARY}"),
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


def _settings_items(s: SettingsBundle | None) -> list[tuple[Content, object]]:
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
            _count_item_label("Env vars", len(s.env)),
            _SettingsItem("Env vars", "dict", dict(s.env)),
        ),
        (
            _count_item_label("Permissions allow", len(s.permissions_allow)),
            _SettingsItem("Permissions allow", "list", list(s.permissions_allow)),
        ),
        (
            _count_item_label("Permissions deny", len(s.permissions_deny)),
            _SettingsItem("Permissions deny", "list", list(s.permissions_deny)),
        ),
        (
            _count_item_label("Permissions ask", len(s.permissions_ask)),
            _SettingsItem("Permissions ask", "list", list(s.permissions_ask)),
        ),
        (
            _count_item_label("Enabled plugins", len(s.enabled_plugins)),
            _SettingsItem("Enabled plugins", "list", list(s.enabled_plugins)),
        ),
        (
            _count_item_label("Hooks dir files", s.hooks_dir_files),
            _SettingsItem("Hooks dir files", "scalar", str(s.hooks_dir_files)),
        ),
    ]


def _scalar_label(name: str, value: object) -> Content:
    if value:
        return Content.assemble(f"{name}: ", str(value))
    return Content.assemble(f"{name}: ", ("—", COLOR_MUTED))


def _count_item_label(name: str, count: int) -> Content:
    style = COLOR_MUTED if count == 0 else ""
    return Content.assemble(f"{name}: ", (str(count), style))


def _settings_detail_widgets(payload: object) -> list[Widget]:
    if not isinstance(payload, _SettingsItem):
        return [Static("(no setting selected)", classes="muted")]
    header = Static(
        _styled(payload.label, f"bold {COLOR_PRIMARY}"), classes="detail-header"
    )
    body = _settings_body(payload)
    if payload.kind in ("dict", "list"):
        return [header, _card("Items", body)]
    return [header, body]


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
    t.add_column(style="dim", justify="right")
    t.add_column(overflow="fold")
    for i, v in enumerate(lst, 1):
        t.add_row(str(i), str(v))
    return Static(t)


# --- Hooks --------------------------------------------------------------


def _hooks_items(hooks: tuple[HookSpec, ...]) -> list[tuple[Content, object]]:
    items: list[tuple[Content, object]] = []
    for h in hooks:
        preview = h.command[:40] + ("…" if len(h.command) > 40 else "")
        label = Content.assemble(
            (h.event, "bold"),
            (f"  [{h.matcher or '*'}]  ", COLOR_MUTED),
            preview,
        )
        items.append((label, h))
    return items


def _hooks_detail_widgets(payload: object) -> list[Widget]:
    if not isinstance(payload, HookSpec):
        return [Static("(no hook selected)", classes="muted")]
    rows: list[tuple[str, RenderableType]] = [
        ("Event", Text(payload.event, style="bold")),
        (
            "Matcher",
            payload.matcher or _muted_cell("(none — runs on all)"),
        ),
        ("Type", payload.type),
        (
            "Timeout",
            str(payload.timeout)
            if payload.timeout is not None
            else _muted_cell("(default)"),
        ),
    ]
    widgets: list[Widget] = [_card("Properties", Static(_kv_table(rows)))]
    widgets.append(
        _card(
            "Command",
            Static(
                Syntax(
                    payload.command,
                    "bash",
                    theme="ansi_dark",
                    word_wrap=True,
                    background_color="default",
                )
            ),
        )
    )
    if payload.referenced_script is not None:
        badge = (
            _badge("exists", "success")
            if payload.script_exists
            else _badge("MISSING", "error")
        )
        script_line = Content.assemble(
            "Script: ",
            badge,
            "  ",
            (str(payload.referenced_script), COLOR_MUTED),
        )
        widgets.append(_card("Script", Static(script_line)))
    return widgets


# --- Slash commands -----------------------------------------------------


def _commands_items(commands: tuple[SlashCommand, ...]) -> list[tuple[Content, object]]:
    items: list[tuple[Content, object]] = []
    for c in commands:
        if c.description:
            label = Content.assemble(
                (f"/{c.name}", "bold"),
                (" — ", COLOR_MUTED),
                c.description,
            )
        else:
            label = Content(f"/{c.name}").stylize("bold")
        items.append((label, c))
    return items


def _commands_detail_widgets(payload: object) -> list[Widget]:
    if not isinstance(payload, SlashCommand):
        return [Static("(no command selected)", classes="muted")]
    if payload.description:
        header_content = Content.assemble(
            (f"/{payload.name}", f"bold {COLOR_PRIMARY}"),
            "  ",
            payload.description,
        )
    else:
        header_content = _styled(f"/{payload.name}", f"bold {COLOR_PRIMARY}")
    widgets: list[Widget] = [Static(header_content, classes="detail-header")]
    tools = ", ".join(payload.allowed_tools) if payload.allowed_tools else "(none)"
    rows: list[tuple[str, RenderableType]] = [
        ("Path", str(payload.path)),
        (
            "Argument hint",
            payload.argument_hint or _muted_cell("(none)"),
        ),
        ("Allowed tools", tools),
    ]
    widgets.append(_card("Properties", Static(_kv_table(rows))))
    widgets.append(_card("Body", Markdown(payload.body or "_(empty)_")))
    return widgets


# --- Plugins ------------------------------------------------------------


def _plugins_items(plugins: tuple[Plugin, ...]) -> list[tuple[Content, object]]:
    items: list[tuple[Content, object]] = []
    for p in plugins:
        state_text, state_color = (
            ("enabled", COLOR_SUCCESS) if p.enabled else ("disabled", COLOR_MUTED)
        )
        label = Content.assemble(
            (p.qualified_id, "bold"),
            "  ",
            (state_text, state_color),
            (f"  [{len(p.installations)} install(s)]", COLOR_MUTED),
        )
        items.append((label, p))
    return items


def _plugins_detail_widgets(payload: object) -> list[Widget]:
    if not isinstance(payload, Plugin):
        return [Static("(no plugin selected)", classes="muted")]
    badge = (
        _badge("enabled", "success") if payload.enabled else _badge("disabled", "muted")
    )
    header_content = Content.assemble(
        (payload.qualified_id, f"bold {COLOR_PRIMARY}"),
        "  ",
        badge,
    )
    widgets: list[Widget] = [Static(header_content, classes="detail-header")]
    rows: list[tuple[str, RenderableType]] = [
        ("ID", payload.id),
        ("Marketplace", payload.marketplace or _muted_cell("(none)")),
    ]
    widgets.append(_card("Properties", Static(_kv_table(rows))))
    title = f"Installations ({len(payload.installations)})"
    if not payload.installations:
        widgets.append(
            _card(title, Static("(no installations on disk)", classes="muted"))
        )
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
            _card(
                title,
                _PendingDataTable(
                    columns=("#", "scope", "version", "git", "path", "project"),
                    rows=install_rows,
                ),
            )
        )
    return widgets


# --- Memory -------------------------------------------------------------

_MEMORY_KIND_LABEL = {
    "claude_md": "CLAUDE",
    "memory_index": "index",
    "memory_entry": "entry",
}


def _memory_items(memory: tuple[MemoryFile, ...]) -> list[tuple[Content, object]]:
    items: list[tuple[Content, object]] = []
    for m in memory:
        kind = _MEMORY_KIND_LABEL.get(m.kind, m.kind)
        parts: list[Content | str | tuple[str, str]] = [
            (kind, "bold"),
            "  ",
            m.path.name,
        ]
        if m.project_label:
            parts.append((f"  @ {m.project_label}", COLOR_MUTED))
        parts.append((f"  ({len(m.body)} chars", COLOR_MUTED))
        if m.has_frontmatter:
            parts.append((" +fm", COLOR_INFO))
        parts.append((")", COLOR_MUTED))
        items.append((Content.assemble(*parts), m))
    return items


def _memory_detail_widgets(payload: object) -> list[Widget]:
    if not isinstance(payload, MemoryFile):
        return [Static("(no memory file selected)", classes="muted")]
    kind_label = _MEMORY_KIND_LABEL.get(payload.kind, payload.kind)
    parts: list[Content | str | tuple[str, str]] = [
        (kind_label, f"bold {COLOR_PRIMARY}"),
        "  ",
        payload.path.name,
    ]
    if payload.has_frontmatter:
        parts.extend(("  ", _badge("+fm", "info")))
    widgets: list[Widget] = [
        Static(Content.assemble(*parts), classes="detail-header"),
    ]
    rows: list[tuple[str, RenderableType]] = [
        (
            "Project",
            payload.project_label or _muted_cell("(user-level)"),
        ),
        ("Path", str(payload.path)),
        ("Size", f"{len(payload.body)} chars"),
    ]
    widgets.append(_card("Properties", Static(_kv_table(rows))))
    widgets.append(_card("Body", Markdown(payload.body or "_(empty)_")))
    return widgets


# --- Keybindings --------------------------------------------------------


def _keybindings_items(
    kb: KeybindingsBundle | None,
) -> list[tuple[Content, object]]:
    if kb is None:
        return []
    items: list[tuple[Content, object]] = []
    for e in kb.entries:
        label = Content.assemble(
            (f"[{e.context}] ", COLOR_MUTED),
            (e.key, "bold"),
            (" → ", COLOR_MUTED),
            e.action,
        )
        items.append((label, e))
    return items


def _keybindings_detail_widgets(payload: object) -> list[Widget]:
    if not isinstance(payload, KeybindingEntry):
        return [Static("(no binding selected)", classes="muted")]
    rows: list[tuple[str, RenderableType]] = [
        ("Context", payload.context),
        ("Key", Text(payload.key, style="bold")),
        ("Action", payload.action),
    ]
    return [_card("Properties", Static(_kv_table(rows)))]


# --- MCP servers --------------------------------------------------------


def _mcp_items(mcp: tuple[MCPServer, ...]) -> list[tuple[Content, object]]:
    items: list[tuple[Content, object]] = []
    for m in mcp:
        label = Content.assemble(
            (m.name, "bold"),
            (f"  ({m.command or '?'})", COLOR_MUTED),
        )
        items.append((label, m))
    return items


def _mcp_detail_widgets(payload: object) -> list[Widget]:
    if not isinstance(payload, MCPServer):
        return [Static("(no MCP server selected)", classes="muted")]
    widgets: list[Widget] = [
        Static(
            _styled(payload.name, f"bold {COLOR_PRIMARY}"),
            classes="detail-header",
        )
    ]
    args = " ".join(payload.args) if payload.args else "(none)"
    rows: list[tuple[str, RenderableType]] = [
        ("Source", str(payload.source_path)),
        ("Command", payload.command or _muted_cell("(none)")),
        ("Args", args),
    ]
    widgets.append(_card("Properties", Static(_kv_table(rows))))
    title = f"Environment ({len(payload.env)})"
    if not payload.env:
        widgets.append(_card(title, Static("(no env vars)", classes="muted")))
    else:
        env_rows: tuple[tuple[str, ...], ...] = tuple(
            (k, redact(payload.env[k])) for k in sorted(payload.env.keys())
        )
        widgets.append(
            _card(
                title,
                _PendingDataTable(
                    columns=("Key", "Value (redacted)"), rows=env_rows
                ),
            )
        )
    return widgets


# --- Scan warnings ------------------------------------------------------


def _warnings_items(
    warnings: tuple[ScanWarning, ...],
) -> list[tuple[Content, object]]:
    items: list[tuple[Content, object]] = []
    for w in warnings:
        sev = warning_severity(w.category)
        color = _SEVERITY_COLOR[sev]
        preview = w.reason[:60] + ("…" if len(w.reason) > 60 else "")
        label = Content.assemble(
            (f"[{w.category}] ", f"bold {color}"),
            preview,
        )
        items.append((label, w))
    return items


def _warnings_detail_widgets(payload: object) -> list[Widget]:
    if not isinstance(payload, ScanWarning):
        return [Static("(no warning selected)", classes="muted")]
    sev = warning_severity(payload.category)
    color = _SEVERITY_COLOR[sev]
    widgets: list[Widget] = [
        Static(
            _styled(payload.category, f"bold {color}"),
            classes="detail-header",
        )
    ]
    if payload.path:
        widgets.append(Static(_styled(str(payload.path), COLOR_MUTED)))
    widgets.append(_card("Reason", Static(payload.reason), severity=sev))
    return widgets
