from collections.abc import Iterable
from pathlib import Path
from typing import Literal, NamedTuple, cast

from rich.console import RenderableType
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.containers import Container
from textual.content import Content
from textual.widget import Widget
from textual.widgets import DataTable, Markdown, Static

from agentpeek.models import (
    HookSpec,
    KeybindingEntry,
    KeybindingsBundle,
    MCPServer,
    MemoryFile,
    Plugin,
    PluginAgent,
    PluginManifest,
    PluginSkill,
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
    ("skills", "Skills"),
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


# Markdown rendering cap. Large bodies (10 MB transcript dumps,
# accidental paste of a log) can stall Textual's Markdown widget. We
# show the leading slice and a marker; the full file is still on disk
# and `o` opens it in $EDITOR.
_BODY_PREVIEW_LIMIT = 256_000


def _bounded_markdown(body: str) -> Markdown:
    if not body:
        return Markdown("_(empty)_")
    if len(body) <= _BODY_PREVIEW_LIMIT:
        return Markdown(body)
    overflow = len(body) - _BODY_PREVIEW_LIMIT
    return Markdown(
        body[:_BODY_PREVIEW_LIMIT]
        + f"\n\n_(truncated — {overflow} more bytes; press `o` to open)_"
    )


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
        # On a fresh mount we add both columns and rows. On remount (e.g.
        # after a rescan re-attaches the detail container), the columns
        # are still around but the rows may be stale — wipe them and
        # re-add so the table reflects the latest payload.
        if self.columns:
            self.clear()
        else:
            self.add_columns(*self._pending_columns)
        for row in self._pending_rows:
            self.add_row(*row)


class _SkillsDataTable(_PendingDataTable):
    """Skills table inside the plugin detail card.

    Carries the `PluginSkill` instances alongside the rows so that
    MainScreen's `on_data_table_row_selected` can resolve a row back
    to a skill and push the SkillDetailModal.
    """

    def __init__(
        self,
        columns: tuple[str, ...],
        rows: tuple[tuple[str, ...], ...],
        skills: tuple["PluginSkill", ...],
    ) -> None:
        super().__init__(columns=columns, rows=rows)
        self.plugin_skills = skills


class _AgentsDataTable(_PendingDataTable):
    """Agents table inside the plugin detail card.

    Mirror of `_SkillsDataTable` — carries `PluginAgent` instances so
    MainScreen can resolve a row back to an agent and push the
    AgentDetailModal.
    """

    def __init__(
        self,
        columns: tuple[str, ...],
        rows: tuple[tuple[str, ...], ...],
        agents: tuple["PluginAgent", ...],
    ) -> None:
        super().__init__(columns=columns, rows=rows)
        self.plugin_agents = agents


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


def scope_summary(report: ScanReport, *, explicit_root: bool = False) -> str:
    """One-token scope hint shared between the sidebar title and the
    app subtitle so both pull from the same source of truth.
    """
    if explicit_root:
        return "custom root"
    if report.user is not None and report.project is not None:
        return "U + P"
    if report.project is not None:
        return "P"
    return "U"


def scope_path(report: ScanReport) -> str:
    """Best path to show as a subtitle hint.

    Prefers the project root over the user root since project scope is
    where most variation lives. Renders home paths with a `~/` prefix.
    """
    target: Path | None = None
    if report.project is not None:
        target = report.project.root
    elif report.user is not None:
        target = report.user.root
    if target is None:
        return ""
    home = Path.home()
    try:
        return f"~/{target.relative_to(home)}"
    except ValueError:
        return str(target)


def item_body(payload: object) -> str | None:
    """Resolve the body text of an item, dispatched by payload type.

    Used by `b` (yank body). Returns None for payloads with no
    natural "body" (settings scalars, plugin rows, keybindings, etc).
    """
    if isinstance(payload, MemoryFile | SlashCommand | PluginSkill | PluginAgent):
        return payload.body
    if isinstance(payload, HookSpec):
        return payload.command
    return None


def item_path(payload: object, result: ScanResult) -> Path | None:  # noqa: PLR0911
    """Resolve the on-disk path for an item, dispatched by payload type.

    Used by `o` (open in $EDITOR) and `y` (yank to clipboard). Some
    categories don't carry their own path (`_SettingsItem`,
    `KeybindingEntry`) — for those we fall back to the parent bundle's
    path from the active `ScanResult`.

    Returns None when the item has no addressable file (e.g. a hook
    referencing an inline shell command with no `referenced_script`,
    or a memory entry with no path — shouldn't happen but defensive).
    """
    if isinstance(payload, MemoryFile | SlashCommand | PluginSkill):
        return payload.path
    if isinstance(payload, HookSpec):
        return payload.referenced_script
    if isinstance(payload, Plugin):
        return (
            payload.installations[0].install_path if payload.installations else None
        )
    if isinstance(payload, MCPServer):
        return payload.source_path
    if isinstance(payload, ScanWarning):
        return payload.path
    if isinstance(payload, KeybindingEntry):
        return result.keybindings.path if result.keybindings else None
    if isinstance(payload, _SettingsItem):
        if result.settings is None:
            return None
        return (
            result.settings.local_settings_path or result.settings.user_settings_path
        )
    return None


def category_count(result: ScanResult, key: str) -> int:
    plugin_commands = sum(len(p.commands) for p in result.plugins)
    plugin_hooks = sum(len(p.hooks) for p in result.plugins)
    plugin_mcps = sum(len(p.mcps) for p in result.plugins)
    counts = {
        "settings": 1 if result.settings else 0,
        "hooks": len(result.hooks) + plugin_hooks,
        "commands": len(result.commands) + plugin_commands,
        "plugins": len(result.plugins),
        "skills": sum(len(p.skills) for p in result.plugins),
        "memory": len(result.memory),
        "keybindings": (len(result.keybindings.entries) if result.keybindings else 0),
        "mcp": len(result.mcp) + plugin_mcps,
        "warnings": len(result.warnings),
    }
    return counts.get(key, 0)


def sidebar_count(report: ScanReport, key: str) -> Content:
    """Build the count portion of a sidebar row.

    Returns just the count badge so the sidebar can render the name on
    the left and the count right-aligned in its own column. Zero counts
    render in `$text-muted`; non-zero in default text; warnings row with
    a non-zero count rendered in `$warning` so attention-needing
    categories stand out.
    """
    user_n = category_count(report.user, key) if report.user else 0
    project_n = category_count(report.project, key) if report.project else 0
    if report.user is not None and report.project is not None:
        return Content.assemble(
            ("U:", COLOR_MUTED),
            (str(user_n), _count_style(key, user_n)),
            (" P:", COLOR_MUTED),
            (str(project_n), _count_style(key, project_n)),
        )
    total = user_n + project_n
    return Content.assemble((f"({total})", _count_style(key, total)))


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

    Labels are prefixed with `[U]` / `[P]` only when both scopes have
    items in this category — when one side is empty the prefix would
    just be noise on every row. The scope is still visible as the
    `[user]` / `[project]` chip on the detail pane.

    The "skills" category is special-cased to group by source plugin
    rather than scope: each plugin becomes a non-selectable header row
    (payload=None) followed by indented skill rows. The plugin name no
    longer needs to repeat per skill, eliminating label redundancy and
    truncation.
    """
    if key == "skills":
        return _skills_grouped_items(report)
    user_items = (
        category_items(report.user, key) if report.user is not None else []
    )
    project_items = (
        category_items(report.project, key) if report.project is not None else []
    )
    need_prefix = bool(user_items) and bool(project_items)
    items: list[tuple[Content, object, str]] = []
    for label, payload in user_items:
        display = _prefix(label, "U", COLOR_PRIMARY) if need_prefix else label
        items.append((display, payload, "user"))
    for label, payload in project_items:
        display = _prefix(label, "P", COLOR_ACCENT) if need_prefix else label
        items.append((display, payload, "project"))
    return items


def _skills_grouped_items(
    report: ScanReport,
) -> list[tuple[Content, object, str]]:
    """Aggregate skills across both scopes, grouped by source plugin.

    Each plugin emits one non-selectable header row (payload = None,
    handled by MainScreen as a divider) followed by its skills,
    indented and without the redundant plugin id prefix.
    """
    items: list[tuple[Content, object, str]] = []
    multi = report.user is not None and report.project is not None
    for scope_name, result in (("user", report.user), ("project", report.project)):
        if result is None:
            continue
        for p in result.plugins:
            if not p.skills:
                continue
            scope_tag = f"  ({scope_name})" if multi else ""
            header = Content.assemble(
                (p.qualified_id, f"bold {COLOR_INFO}"),
                (scope_tag, COLOR_MUTED),
            )
            items.append((header, None, scope_name))
            for s in p.skills:
                label = Content.assemble(
                    ("  ", ""),  # 2-space indent under the group header
                    (s.name, "bold"),
                    (" — ", COLOR_MUTED),
                    s.description or "",
                )
                items.append((label, s, scope_name))
    return items


def _prefix(label: Content, marker: str, color: str) -> Content:
    return Content.assemble(
        (f"[{marker}] ", f"bold {color}"),
        label,
    )


def _plug_prefix(label: Content, source_plugin: str | None) -> Content:
    """Prepend a `[plug:<id>] ` provenance segment when source_plugin is set.

    Used by the global Commands / Hooks / MCP categories to indicate
    plugin-contributed entries. Yellow (`$warning`) to stay visually
    distinct from `[U]` (`$primary` blue) and `[P]` (`$accent`).
    """
    if source_plugin is None:
        return label
    return Content.assemble(
        (f"[plug:{source_plugin}] ", f"bold {COLOR_WARNING}"),
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
            merged_hooks = result.hooks + tuple(
                h for p in result.plugins for h in p.hooks
            )
            return _hooks_items(merged_hooks)
        case "commands":
            merged_commands = result.commands + tuple(
                c for p in result.plugins for c in p.commands
            )
            return _commands_items(merged_commands)
        case "plugins":
            return _plugins_items(result.plugins)
        case "skills":
            return _skills_items(result.plugins)
        case "memory":
            return _memory_items(result.memory)
        case "keybindings":
            return _keybindings_items(result.keybindings)
        case "mcp":
            merged_mcps = result.mcp + tuple(
                m for p in result.plugins for m in p.mcps
            )
            return _mcp_items(merged_mcps)
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
        case "skills":
            return _skills_detail_widgets(payload)
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
            _scalar_label("Output style", s.output_style),
            _SettingsItem("Output style", "scalar", s.output_style),
        ),
        (
            _scalar_label(
                "Skip auto permission prompt",
                "yes" if s.skip_auto_permission_prompt else None,
            ),
            _SettingsItem(
                "Skip auto permission prompt",
                "scalar",
                "yes" if s.skip_auto_permission_prompt else None,
            ),
        ),
        (
            _count_item_label(
                "Status line", 1 if s.status_line else 0
            ),
            _SettingsItem(
                "Status line", "dict", dict(s.status_line) if s.status_line else {}
            ),
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
            _count_item_label("Policy restrictions", len(s.policy_restrictions)),
            _SettingsItem(
                "Policy restrictions",
                "dict",
                dict(s.policy_restrictions),
            ),
        ),
        (
            _count_item_label("Company announcements", len(s.company_announcements)),
            _SettingsItem(
                "Company announcements", "list", list(s.company_announcements)
            ),
        ),
        (
            _count_item_label("Spinner tips override", len(s.spinner_tips)),
            _SettingsItem("Spinner tips override", "list", list(s.spinner_tips)),
        ),
        (
            _count_item_label("Hooks dir files", len(s.hooks_dir_files)),
            _SettingsItem(
                "Hooks dir files", "list", list(s.hooks_dir_files)
            ),
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
        return Static("—", classes="muted")
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


_PREVIEW_TRUNC_MARK = " […]"


def _truncate_preview(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + _PREVIEW_TRUNC_MARK


def _hooks_items(hooks: tuple[HookSpec, ...]) -> list[tuple[Content, object]]:
    items: list[tuple[Content, object]] = []
    for h in hooks:
        label = Content.assemble(
            (h.event, "bold"),
            (f"  [{h.matcher or '*'}]  ", COLOR_MUTED),
            _truncate_preview(h.command, 40),
        )
        items.append((_plug_prefix(label, h.source_plugin), h))
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
    if payload.referenced_dynamic:
        rows.append(("Dynamic vars", Text("yes", style="bold yellow")))
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
        items.append((_plug_prefix(label, c.source_plugin), c))
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
    tools = ", ".join(payload.allowed_tools) if payload.allowed_tools else "—"
    rows: list[tuple[str, RenderableType]] = [
        ("Path", str(payload.path)),
        (
            "Argument hint",
            payload.argument_hint or _muted_cell("—"),
        ),
        ("Allowed tools", tools),
    ]
    widgets.append(_card("Properties", Static(_kv_table(rows))))
    widgets.append(_card("Body", _bounded_markdown(payload.body)))
    return widgets


# --- Plugins ------------------------------------------------------------


def _plugins_items(plugins: tuple[Plugin, ...]) -> list[tuple[Content, object]]:
    items: list[tuple[Content, object]] = []
    for p in plugins:
        state_text, state_color = (
            ("enabled", COLOR_SUCCESS) if p.enabled else ("disabled", COLOR_MUTED)
        )
        parts: list[Content | str | tuple[str, str]] = [
            (p.qualified_id, "bold"),
            "  ",
            (state_text, state_color),
            (f"  [{len(p.installations)} install(s)]", COLOR_MUTED),
        ]
        if p.blocked:
            parts.extend(("  ", ("[BLOCKED]", f"bold {COLOR_ERROR}")))
        items.append((Content.assemble(*parts), p))
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
        ("Marketplace", payload.marketplace or _muted_cell("—")),
    ]
    src = payload.marketplace_source
    if src:
        source_kind = src.get("source", "")
        repo = src.get("repo", "")
        ref = src.get("ref", "")
        if source_kind and repo:
            source_line = f"{source_kind}:{repo}" + (f"@{ref}" if ref else "")
            rows.append(("Source", source_line))
        if src.get("installLocation"):
            rows.append(("Cached at", src["installLocation"]))
        if src.get("lastUpdated"):
            rows.append(("Marketplace updated", src["lastUpdated"]))
    if payload.blocked:
        rows.append(
            (
                "Blocklisted",
                Text(
                    payload.blocked_reason or "(no reason given)",
                    style="bold red",
                ),
            )
        )
    widgets.append(_card("Properties", Static(_kv_table(rows))))
    if payload.manifest is not None:
        widgets.append(_card("Manifest", Static(_manifest_table(payload.manifest))))
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
    # Content cards — only render when present. Each is a bounded
    # DataTable so a 14-skill plugin doesn't take over the pane.
    if payload.skills:
        widgets.append(
            _card(
                f"Skills ({len(payload.skills)})",
                _SkillsDataTable(
                    columns=("name", "description"),
                    rows=tuple(
                        (s.name, s.description or "") for s in payload.skills
                    ),
                    skills=payload.skills,
                ),
            )
        )
    if payload.agents:
        widgets.append(
            _card(
                f"Agents ({len(payload.agents)})",
                _AgentsDataTable(
                    columns=("name", "description"),
                    rows=tuple(
                        (a.name, a.description or "") for a in payload.agents
                    ),
                    agents=payload.agents,
                ),
            )
        )
    if payload.commands:
        widgets.append(
            _card(
                f"Commands ({len(payload.commands)})",
                _PendingDataTable(
                    columns=("name", "description"),
                    rows=tuple(
                        (f"/{c.name}", c.description or "") for c in payload.commands
                    ),
                ),
            )
        )
    if payload.hooks:
        widgets.append(
            _card(
                f"Hooks ({len(payload.hooks)})",
                _PendingDataTable(
                    columns=("event", "matcher", "command"),
                    rows=tuple(
                        (
                            h.event,
                            h.matcher or "*",
                            _truncate_preview(h.command, 60),
                        )
                        for h in payload.hooks
                    ),
                ),
            )
        )
    if payload.mcps:
        widgets.append(
            _card(
                f"MCP servers ({len(payload.mcps)})",
                _PendingDataTable(
                    columns=("name", "command"),
                    rows=tuple((m.name, m.command or "?") for m in payload.mcps),
                ),
            )
        )
    return widgets


def _manifest_table(m: PluginManifest) -> Table:
    rows: list[tuple[str, RenderableType]] = []
    if m.name:
        rows.append(("Name", m.name))
    if m.description:
        rows.append(("Description", m.description))
    if m.version:
        rows.append(("Version", m.version))
    if m.author_name:
        author = m.author_name
        if m.author_email:
            author = f"{author} <{m.author_email}>"
        rows.append(("Author", author))
    if m.homepage:
        rows.append(("Homepage", m.homepage))
    if m.repository:
        rows.append(("Repository", m.repository))
    if m.license:
        rows.append(("License", m.license))
    if m.keywords:
        rows.append(("Keywords", ", ".join(m.keywords)))
    if m.requires:
        rows.append(("Requires", ", ".join(m.requires)))
    if not rows:
        rows.append(("(manifest)", _muted_cell("(no fields)")))
    return _kv_table(rows)


# --- Skills (aggregated across plugins) ---------------------------------


def _skills_items(plugins: tuple[Plugin, ...]) -> list[tuple[Content, object]]:
    """Flatten every plugin's `.skills` into a single ordered list,
    each row prefixed with the contributing plugin's qualified id."""
    items: list[tuple[Content, object]] = []
    for p in plugins:
        for s in p.skills:
            label = Content.assemble(
                (f"[plug:{p.qualified_id}] ", f"bold {COLOR_INFO}"),
                (s.name, "bold"),
                (" — ", COLOR_MUTED),
                s.description or "",
            )
            items.append((label, s))
    return items


def _skills_detail_widgets(payload: object) -> list[Widget]:
    if not isinstance(payload, PluginSkill):
        return [Static("(no skill selected)", classes="muted")]
    header = Content.assemble((payload.name, f"bold {COLOR_PRIMARY}"))
    widgets: list[Widget] = [Static(header, classes="detail-header")]
    rows: list[tuple[str, RenderableType]] = [
        (
            "Source plugin",
            payload.source_plugin or _muted_cell("(unknown)"),
        ),
        (
            "Description",
            payload.description or _muted_cell("—"),
        ),
        ("Path", str(payload.path)),
    ]
    if payload.user_invocable is not None:
        rows.append(("User-invocable", "yes" if payload.user_invocable else "no"))
    if payload.when_to_use:
        rows.append(("When to use", payload.when_to_use))
    if payload.allowed_tools:
        rows.append(("Allowed tools", ", ".join(payload.allowed_tools)))
    if payload.disallowed_tools:
        rows.append(("Disallowed tools", ", ".join(payload.disallowed_tools)))
    widgets.append(_card("Properties", Static(_kv_table(rows))))
    widgets.append(_card("Body", _bounded_markdown(payload.body)))
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
    widgets.append(_card("Body", _bounded_markdown(payload.body)))
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
        parts: list[Content | str | tuple[str, str]] = [
            (m.name, "bold"),
            (f"  ({m.command or 'oauth'})", COLOR_MUTED),
        ]
        if m.auth_pending:
            parts.extend(("  ", ("[auth pending]", f"bold {COLOR_WARNING}")))
        items.append((_plug_prefix(Content.assemble(*parts), m.source_plugin), m))
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
    args = " ".join(payload.args) if payload.args else "—"
    rows: list[tuple[str, RenderableType]] = [
        ("Source", str(payload.source_path)),
    ]
    if payload.source_plugin:
        rows.append(("Source plugin", payload.source_plugin))
    rows.extend(
        [
            ("Command", payload.command or _muted_cell("(none — OAuth-based)")),
            ("Args", args),
        ]
    )
    if payload.auth_pending:
        rows.append(
            (
                "Auth",
                Text(
                    "pending — Claude Code will not connect until OAuth completes",
                    style="bold yellow",
                ),
            )
        )
    widgets.append(_card("Properties", Static(_kv_table(rows))))
    title = f"Environment ({len(payload.env)})"
    if not payload.env:
        widgets.append(_card(title, Static("(empty)", classes="muted")))
    else:
        # `redact()` only masks values of length ≥ 8 — short values like
        # "true" / "on" pass through. Surface that contract honestly
        # instead of labelling the column "redacted" when some rows
        # aren't.
        env_rows: tuple[tuple[str, ...], ...] = tuple(
            (k, redact(payload.env[k])) for k in sorted(payload.env.keys())
        )
        widgets.append(
            _card(
                title,
                Container(
                    _PendingDataTable(columns=("Key", "Value"), rows=env_rows),
                    Static(
                        "(values ≥ 8 chars are masked)",
                        classes="muted",
                    ),
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
        preview = _truncate_preview(w.reason, 60)
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
