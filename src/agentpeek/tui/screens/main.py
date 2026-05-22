import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
)

from agentpeek.actions.runner import ActionResult, PluginVerb, Scope, run_plugin
from agentpeek.models import Plugin, PluginAgent, PluginSkill, ScanReport
from agentpeek.tui.render import (
    CATEGORIES,
    item_body,
    item_path,
    items_for_report,
    render_detail_widgets,
    scope_summary,
    sidebar_count,
)
from agentpeek.tui.screens.action_result import ActionResultModal
from agentpeek.tui.screens.agent_detail import AgentDetailModal
from agentpeek.tui.screens.confirm import ConfirmModal
from agentpeek.tui.screens.help import HelpScreen
from agentpeek.tui.screens.skill_detail import SkillDetailModal

if TYPE_CHECKING:
    from agentpeek.tui.app import AgentViewApp


class MainScreen(Screen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("o", "open", "Open"),
        Binding("y", "yank", "Yank path"),
        Binding("b", "yank_body", "Yank body"),
        Binding("u", "update_plugin", "Update plugin"),
        Binding("U", "update_all_plugins", "Update all"),
        Binding("t", "toggle_plugin", "Toggle enabled"),
        Binding("x", "uninstall_plugin", "Uninstall plugin"),
        Binding("slash", "focus_filter", "Filter"),
        Binding("question_mark", "help", "Help"),
        Binding("escape", "clear_filter", show=False),
    ]

    selected_category: reactive[str] = reactive(CATEGORIES[0][0], init=False)
    selected_index: reactive[int] = reactive(-1, init=False)
    filter_text: reactive[str] = reactive("", init=False)

    def __init__(
        self,
        report: ScanReport,
        *,
        explicit_root: bool = False,
        actions: bool = False,
    ) -> None:
        super().__init__()
        self._report = report
        self._explicit_root = explicit_root
        self._actions_enabled = actions
        # Per-category cursor memory so switching tabs preserves position.
        self._category_state: dict[str, int] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="three-zone"):
            with Vertical(id="sidebar"):
                yield Label(self._sidebar_title(), classes="zone-title")
                yield ListView(
                    *[
                        ListItem(
                            Horizontal(
                                Label(name, classes="sidebar-name"),
                                Label(
                                    sidebar_count(self._report, key),
                                    classes="sidebar-count",
                                ),
                            ),
                            name=key,
                        )
                        for key, name in CATEGORIES
                    ],
                    id="category-list",
                )
            with Vertical(id="main-panel"):
                yield Label("Items", classes="zone-title", id="main-title")
                filter_input = Input(placeholder="filter…", id="filter-input")
                filter_input.display = False
                yield filter_input
                yield ListView(id="item-list")
            with VerticalScroll(id="detail-pane"):
                yield Label("Detail", classes="zone-title")
                yield Container(id="detail-body")
        yield Footer()

    def _sidebar_title(self) -> str:
        return (
            f"Categories  "
            f"({scope_summary(self._report, explicit_root=self._explicit_root)})"
        )

    def on_mount(self) -> None:
        # Trigger initial population by re-assigning the reactive default.
        self.selected_category = CATEGORIES[0][0]

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        list_id = event.list_view.id
        if list_id == "category-list":
            if event.item is not None and event.item.name is not None:
                self.selected_category = event.item.name
        elif list_id == "item-list":
            idx = event.list_view.index
            if idx is not None:
                self.selected_index = idx

    async def watch_selected_category(self, _category: str) -> None:
        await self._rebuild_items()

    async def watch_selected_index(self, idx: int) -> None:
        if idx >= 0:
            self._category_state[self.selected_category] = idx
        await self._refresh_detail()

    async def watch_filter_text(self, _text: str) -> None:
        await self._rebuild_items()

    async def _rebuild_items(self) -> None:
        """Rebuild the items list for the active category, honoring filter.

        Items with `payload=None` are group headers (used by the Skills
        category) — they get the .group-header class and are marked
        disabled so they don't take focus. Filtering only matches against
        selectable rows; headers are dropped when a filter is active.
        """
        item_list = self.query_one("#item-list", ListView)
        all_items = items_for_report(self._report, self.selected_category)
        total_selectable = sum(1 for it in all_items if it[1] is not None)
        if self.filter_text:
            needle = self.filter_text.lower()
            items = [
                it
                for it in all_items
                if it[1] is not None and needle in it[0].plain.lower()
            ]
        else:
            items = all_items
        await item_list.clear()
        first_selectable: int | None = None
        for i, (label, payload, _scope) in enumerate(items):
            list_item = ListItem(Label(label))
            if payload is None:
                list_item.disabled = True
                list_item.add_class("group-header")
            elif first_selectable is None:
                first_selectable = i
            item_list.append(list_item)
        title = self.query_one("#main-title", Label)
        name = next(
            (n for k, n in CATEGORIES if k == self.selected_category),
            self.selected_category,
        )
        selectable_count = sum(1 for it in items if it[1] is not None)
        if self.filter_text:
            title.update(
                f"{name}  (filtered: {selectable_count} / {total_selectable})"
            )
        else:
            title.update(f"{name}  ({selectable_count})")
        # Restore prior cursor position for this category when it still
        # points to a selectable (non-header) row; otherwise fall back to
        # the first selectable row.
        target: int | None = None
        saved = self._category_state.get(self.selected_category)
        if (
            saved is not None
            and 0 <= saved < len(items)
            and items[saved][1] is not None
        ):
            target = saved
        elif first_selectable is not None:
            target = first_selectable
        if target is not None:
            item_list.index = target
            self.selected_index = target
        else:
            self.selected_index = -1
        await self._refresh_detail()

    async def _refresh_detail(self) -> None:
        container = self.query_one("#detail-body", Container)
        items = items_for_report(self._report, self.selected_category)
        idx = self.selected_index
        await container.remove_children()
        if 0 <= idx < len(items):
            _label, payload, scope = items[idx]
            if payload is None:
                # Group header — nothing to drill into.
                await container.mount(
                    Static("(select a skill)", classes="muted")
                )
                return
            widgets = render_detail_widgets(self.selected_category, payload, scope)
            if widgets:
                await container.mount_all(widgets)
        else:
            await container.mount(Static("(no item selected)", classes="muted"))

    def _current_path(self) -> Path | None:
        """Resolve the path of the currently highlighted item, if any."""
        items = items_for_report(self._report, self.selected_category)
        idx = self.selected_index
        if not 0 <= idx < len(items):
            return None
        _label, payload, scope = items[idx]
        result = self._report.user if scope == "user" else self._report.project
        if result is None:
            return None
        return item_path(payload, result)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Pop a detail modal when the user presses Enter on a Skills or
        Agents row inside a plugin's detail card.

        Resolved by duck-typing the table — only the `_SkillsDataTable`
        carries `plugin_skills`, only `_AgentsDataTable` carries
        `plugin_agents`. Other plugin-detail DataTables (installations,
        hooks, commands, mcps) leave this event a no-op.
        """
        table = event.data_table  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
        idx = event.cursor_row
        app = cast("AgentViewApp", self.app)  # pyright: ignore[reportUnknownMemberType]
        skills_attr = getattr(table, "plugin_skills", None)  # pyright: ignore[reportUnknownArgumentType]
        if isinstance(skills_attr, tuple):
            skills = cast("tuple[PluginSkill, ...]", skills_attr)
            if 0 <= idx < len(skills):
                app.push_screen(SkillDetailModal(skills[idx]))
            return
        agents_attr = getattr(table, "plugin_agents", None)  # pyright: ignore[reportUnknownArgumentType]
        if isinstance(agents_attr, tuple):
            agents = cast("tuple[PluginAgent, ...]", agents_attr)
            if 0 <= idx < len(agents):
                app.push_screen(AgentDetailModal(agents[idx]))

    def action_help(self) -> None:
        """Open a help modal listing every shown Binding."""
        app = cast("AgentViewApp", self.app)  # pyright: ignore[reportUnknownMemberType]
        bindings: list[tuple[str, str]] = []
        for b in self.BINDINGS:
            if isinstance(b, Binding) and b.show:
                bindings.append((b.key, b.description))
        for b in app.BINDINGS:
            if isinstance(b, Binding) and b.show:
                bindings.append((b.key, b.description))
        app.push_screen(HelpScreen(tuple(bindings)))

    def action_focus_filter(self) -> None:
        """Show + focus the filter input."""
        flt = self.query_one("#filter-input", Input)
        flt.display = True
        flt.focus()

    def action_clear_filter(self) -> None:
        """Escape: clear the filter, hide the input, refocus the items list."""
        flt = self.query_one("#filter-input", Input)
        if not flt.has_focus and not self.filter_text:
            return
        flt.value = ""
        flt.display = False
        self.query_one("#item-list", ListView).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self.filter_text = event.value

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter-input":
            self.query_one("#item-list", ListView).focus()

    def action_yank(self) -> None:
        """Copy the highlighted item's path to the clipboard via OSC 52."""
        path = self._current_path()
        if path is None:
            self.notify("No path to copy", severity="warning", timeout=2)
            return
        app = cast("AgentViewApp", self.app)  # pyright: ignore[reportUnknownMemberType]
        app.copy_to_clipboard(str(path))
        self.notify(f"Copied {path}", timeout=2)

    def action_yank_body(self) -> None:
        """Copy the highlighted item's body text to the clipboard.

        Works for memory entries, slash commands, plugin skills, plugin
        agents (their body field), and hooks (their command string).
        """
        items = items_for_report(self._report, self.selected_category)
        idx = self.selected_index
        if not 0 <= idx < len(items):
            self.notify("No item selected", severity="warning", timeout=2)
            return
        _label, payload, _scope = items[idx]
        body = item_body(payload)
        if not body:
            self.notify("No body to copy", severity="warning", timeout=2)
            return
        app = cast("AgentViewApp", self.app)  # pyright: ignore[reportUnknownMemberType]
        app.copy_to_clipboard(body)
        self.notify(f"Copied body ({len(body)} chars)", timeout=2)

    def action_open(self) -> None:
        """Open the highlighted item's file in $EDITOR (suspending the TUI)."""
        path = self._current_path()
        if path is None:
            self.notify("No file path for this item", severity="warning", timeout=2)
            return
        editor = os.environ.get("EDITOR", "vi")
        app = cast("AgentViewApp", self.app)  # pyright: ignore[reportUnknownMemberType]
        with app.suspend():
            subprocess.run([editor, str(path)], check=False)

    async def action_refresh(self) -> None:
        """Re-scan disk and rebuild every list/count/detail in place."""
        prev_label = self._current_item_label()
        app = cast("AgentViewApp", self.app)  # pyright: ignore[reportUnknownMemberType]
        self._report = app.rescan()
        for item in self.query("#category-list > ListItem").results():
            key = item.name
            if key is None:
                continue
            count_label = item.query_one(".sidebar-count", Label)
            count_label.update(sidebar_count(self._report, key))
        self.query_one("#sidebar > .zone-title", Label).update(self._sidebar_title())
        # Rebuild items + detail for the active category.
        await self.watch_selected_category(self.selected_category)
        if prev_label is not None:
            current = self._current_item_label()
            if current != prev_label:
                self.notify(
                    f"Selection reset (was: {prev_label})",
                    severity="warning",
                    timeout=3,
                )
                return
        self.notify("Rescanned", timeout=2)

    def _current_item_label(self) -> str | None:
        items = items_for_report(self._report, self.selected_category)
        idx = self.selected_index
        if 0 <= idx < len(items):
            label, payload, _scope = items[idx]
            if payload is not None:
                return label.plain
        return None

    # --- write actions (claude plugin CLI) ----------------------------------

    def _current_plugin_and_scope(self) -> tuple[Plugin, Scope] | None:
        """Resolve current row to (Plugin, scope) if it's a plugin row.

        Returns None and notifies the user otherwise. Used by all per-plugin
        action handlers.
        """
        if self.selected_category != "plugins":
            self.notify(
                "Plugin actions only apply in the Plugins category",
                severity="warning",
                timeout=2,
            )
            return None
        items = items_for_report(self._report, self.selected_category)
        idx = self.selected_index
        if not 0 <= idx < len(items):
            self.notify("No plugin selected", severity="warning", timeout=2)
            return None
        _label, payload, scope = items[idx]
        if not isinstance(payload, Plugin):
            self.notify("No plugin selected", severity="warning", timeout=2)
            return None
        if scope not in ("user", "project", "local"):
            self.notify(f"Unsupported scope: {scope}", severity="error", timeout=2)
            return None
        return payload, cast("Scope", scope)

    def _require_actions(self) -> bool:
        if not self._actions_enabled:
            self.notify(
                "Write actions disabled — relaunch with --actions",
                severity="warning",
                timeout=3,
            )
            return False
        return True

    def action_update_plugin(self) -> None:
        if not self._require_actions():
            return
        pair = self._current_plugin_and_scope()
        if pair is None:
            return
        plugin, scope = pair
        self.notify(f"Updating {plugin.qualified_id}…", timeout=2)
        self._run_plugin_action("update", plugin.qualified_id, scope)

    def action_toggle_plugin(self) -> None:
        if not self._require_actions():
            return
        pair = self._current_plugin_and_scope()
        if pair is None:
            return
        plugin, scope = pair
        verb: PluginVerb = "disable" if plugin.enabled else "enable"
        self.notify(f"{verb.title()} {plugin.qualified_id}…", timeout=2)
        self._run_plugin_action(verb, plugin.qualified_id, scope)

    def action_uninstall_plugin(self) -> None:
        if not self._require_actions():
            return
        pair = self._current_plugin_and_scope()
        if pair is None:
            return
        plugin, scope = pair
        qid = plugin.qualified_id

        def _after_confirm(ok: bool | None) -> None:
            if not ok:
                return
            self.notify(f"Uninstalling {qid}…", timeout=2)
            self._run_plugin_action("uninstall", qid, scope)

        self.app.push_screen(  # pyright: ignore[reportUnknownMemberType]
            ConfirmModal(
                f"Uninstall {qid} from {scope} scope?\n\nThis removes the plugin "
                "cache and registry entry. Run again to reinstall.",
                title="Uninstall plugin?",
            ),
            _after_confirm,
        )

    @work(exclusive=True, group="plugin-action")
    async def _run_plugin_action(
        self, verb: PluginVerb, qid: str, scope: Scope
    ) -> None:
        import asyncio  # noqa: PLC0415

        result = await asyncio.to_thread(run_plugin, verb, qid, scope=scope)
        await self._after_plugin_action(result)

    async def _after_plugin_action(self, result: ActionResult) -> None:
        await self.action_refresh()
        if result.ok:
            self.notify(
                f"{result.verb} {result.target}: {result.message}",
                timeout=4,
            )
        else:
            self.app.push_screen(  # pyright: ignore[reportUnknownMemberType]
                ActionResultModal(result)
            )

    def action_update_all_plugins(self) -> None:
        if not self._require_actions():
            return
        if self.selected_category != "plugins":
            self.notify(
                "Bulk update only applies in the Plugins category",
                severity="warning",
                timeout=2,
            )
            return
        pairs = self._all_plugin_targets()
        if not pairs:
            self.notify("No plugins to update", severity="warning", timeout=2)
            return

        def _after_confirm(ok: bool | None) -> None:
            if not ok:
                return
            self.notify(
                f"Updating {len(pairs)} plugins serially "
                "(~5-7s each, no progress display)…",
                timeout=4,
            )
            self._run_bulk_update(pairs)

        if len(pairs) > 5:
            self.app.push_screen(  # pyright: ignore[reportUnknownMemberType]
                ConfirmModal(
                    f"Update {len(pairs)} plugins serially?\n"
                    "Each call can take a few seconds (git pull).",
                    title="Update all plugins?",
                ),
                _after_confirm,
            )
        else:
            _after_confirm(True)

    def _all_plugin_targets(self) -> list[tuple[str, Scope]]:
        """Collect (qualified_id, scope) pairs for every installed plugin row.

        Mirrors what the user sees in the Plugins category: group headers
        (payload=None) are skipped; per-scope rows are emitted in display
        order. The same plugin appearing in both user and project scope
        yields two pairs — that matches what `claude plugin update --scope`
        needs (it's scope-specific).
        """
        pairs: list[tuple[str, Scope]] = []
        for _label, payload, scope in items_for_report(self._report, "plugins"):
            if not isinstance(payload, Plugin):
                continue
            if scope not in ("user", "project", "local"):
                continue
            pairs.append((payload.qualified_id, cast("Scope", scope)))
        return pairs

    @work(exclusive=True, group="bulk-update")
    async def _run_bulk_update(self, pairs: list[tuple[str, Scope]]) -> None:
        """Serial bulk-update worker.

        Progress is surfaced via `app.sub_title` (the header subtitle).
        Textual 8.x's `ModalScreen` has a dismissal-deadlock for the
        modal-with-scrollable-content shape we'd otherwise want here
        (issues #5596, #5008, #4552); the subtitle gives live progress
        without any modal lifecycle.
        """
        import asyncio  # noqa: PLC0415

        original_subtitle = self.app.sub_title
        ok_count = 0
        fail_count = 0
        failures: list[str] = []
        total = len(pairs)
        try:
            for i, (qid, scope) in enumerate(pairs, start=1):
                self.app.sub_title = (
                    f"bulk-update ({i}/{total}) {qid} [{scope}]…"
                )
                result = await asyncio.to_thread(
                    run_plugin, "update", qid, scope=scope
                )
                if result.ok:
                    ok_count += 1
                else:
                    fail_count += 1
                    failures.append(f"{qid}: {result.message}")
        finally:
            self.app.sub_title = original_subtitle
        summary = (
            f"Bulk update done — {ok_count} ok, {fail_count} failed. "
            "Press r to refresh, restart Claude Code to apply."
        )
        if failures:
            summary += "\n\nFailures:\n" + "\n".join(failures[:5])
            if len(failures) > 5:
                summary += f"\n…(+{len(failures) - 5} more)"
        self.notify(summary, timeout=15, severity="warning" if failures else "information")
