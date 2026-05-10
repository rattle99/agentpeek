from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from agentview.models import ScanResult
from agentview.tui.render import (
    CATEGORIES,
    category_count,
    category_items,
    render_detail,
)


class MainScreen(Screen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    selected_category: reactive[str] = reactive(CATEGORIES[0][0], init=False)
    selected_index: reactive[int] = reactive(-1, init=False)

    def __init__(self, result: ScanResult) -> None:
        super().__init__()
        self._result = result

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="three-zone"):
            with Vertical(id="sidebar"):
                yield Label("Categories", classes="zone-title")
                yield ListView(
                    *[
                        ListItem(
                            Label(f"{name}  ({category_count(self._result, key)})"),
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
        items = category_items(self._result, category)
        item_list.clear()
        for label, _payload in items:
            item_list.append(ListItem(Label(label)))

        title = self.query_one("#main-title", Label)
        name = next((n for k, n in CATEGORIES if k == category), category)
        title.update(f"{name}  ({len(items)})")

        if items:
            item_list.index = 0
            self.selected_index = 0
        else:
            self.selected_index = -1
        # Always refresh detail directly: changing categories may land on the
        # same numeric index as before, in which case watch_selected_index
        # wouldn't fire and the pane would stay showing the old category.
        self._refresh_detail()

    def watch_selected_index(self, _idx: int) -> None:
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        detail = self.query_one("#detail-content", Static)
        items = category_items(self._result, self.selected_category)
        idx = self.selected_index
        if 0 <= idx < len(items):
            _label, payload = items[idx]
            detail.update(render_detail(self.selected_category, payload))
        else:
            detail.update("(no item selected)")
