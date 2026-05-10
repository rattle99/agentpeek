from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from agentview.models import ScanReport
from agentview.tui.render import (
    CATEGORIES,
    items_for_report,
    render_detail,
    sidebar_label,
)


class MainScreen(Screen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    selected_category: reactive[str] = reactive(CATEGORIES[0][0], init=False)
    selected_index: reactive[int] = reactive(-1, init=False)

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
                            Label(sidebar_label(self._report, name, key)),
                            name=key,
                        )
                        for key, name in CATEGORIES
                    ],
                    id="category-list",
                )
            with Vertical(id="main-panel"):
                yield Label("Items", classes="zone-title", id="main-title")
                yield ListView(id="item-list")
            with VerticalScroll(id="detail-pane"):
                yield Label("Detail", classes="zone-title")
                yield Static(id="detail-content")
        yield Footer()

    def _sidebar_title(self) -> str:
        if self._explicit_root:
            return "Categories  (custom root)"
        if self._report.user is not None and self._report.project is not None:
            return "Categories  (U + P)"
        if self._report.project is not None:
            return "Categories  (project)"
        return "Categories  (user)"

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

    def watch_selected_category(self, category: str) -> None:
        item_list = self.query_one("#item-list", ListView)
        items = items_for_report(self._report, category)
        item_list.clear()
        for label, _payload, _scope in items:
            item_list.append(ListItem(Label(label)))

        title = self.query_one("#main-title", Label)
        name = next((n for k, n in CATEGORIES if k == category), category)
        title.update(f"{name}  ({len(items)})")

        if items:
            item_list.index = 0
            self.selected_index = 0
        else:
            self.selected_index = -1
        self._refresh_detail()

    def watch_selected_index(self, _idx: int) -> None:
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        detail = self.query_one("#detail-content", Static)
        items = items_for_report(self._report, self.selected_category)
        idx = self.selected_index
        if 0 <= idx < len(items):
            _label, payload, scope = items[idx]
            detail.update(render_detail(self.selected_category, payload, scope))
        else:
            detail.update("(no item selected)")
