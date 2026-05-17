import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

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

from agentpeek.models import PluginSkill, ScanReport
from agentpeek.tui.render import (
    CATEGORIES,
    item_path,
    items_for_report,
    render_detail_widgets,
    scope_summary,
    sidebar_count,
)
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
        Binding("slash", "focus_filter", "Filter"),
        Binding("question_mark", "help", "Help"),
        Binding("escape", "clear_filter", show=False),
    ]

    selected_category: reactive[str] = reactive(CATEGORIES[0][0], init=False)
    selected_index: reactive[int] = reactive(-1, init=False)
    filter_text: reactive[str] = reactive("", init=False)

    def __init__(self, report: ScanReport, *, explicit_root: bool = False) -> None:
        super().__init__()
        self._report = report
        self._explicit_root = explicit_root

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

    async def watch_selected_index(self, _idx: int) -> None:
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
        items = items_for_report(self._report, self.selected_category)
        if self.filter_text:
            needle = self.filter_text.lower()
            items = [
                it
                for it in items
                if it[1] is not None and needle in it[0].plain.lower()
            ]
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
        title.update(f"{name}  ({selectable_count})")
        if first_selectable is not None:
            item_list.index = first_selectable
            self.selected_index = first_selectable
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
        """Pop SkillDetailModal when the user presses Enter on a row of
        the plugin-detail's Skills card.

        Only the Skills table (`_SkillsDataTable`) carries a
        `plugin_skills` attribute; other plugin-detail DataTables
        (installations, hooks, commands, mcps) leave this event a no-op.
        """
        table = event.data_table  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
        skills_attr = getattr(table, "plugin_skills", None)  # pyright: ignore[reportUnknownArgumentType]
        if not isinstance(skills_attr, tuple):
            return
        skills = cast("tuple[PluginSkill, ...]", skills_attr)
        idx = event.cursor_row
        if not 0 <= idx < len(skills):
            return
        app = cast("AgentViewApp", self.app)  # pyright: ignore[reportUnknownMemberType]
        app.push_screen(SkillDetailModal(skills[idx]))

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
        self.notify("Rescanned", timeout=2)
