from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from agentview.models import ScanReport
from agentview.tui.render import (
    CATEGORIES,
    items_for_report,
    render_detail_widgets,
    scope_summary,
    sidebar_count,
)

if TYPE_CHECKING:
    from agentview.tui.app import AgentViewApp


class MainScreen(Screen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("r", "refresh", "Refresh"),
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

    async def watch_selected_category(self, category: str) -> None:
        item_list = self.query_one("#item-list", ListView)
        items = items_for_report(self._report, category)
        await item_list.clear()
        for label, _payload, _scope in items:
            # `label` is a styled rich.text.Text — brackets are literal
            # segments already, so no markup escaping is needed.
            item_list.append(ListItem(Label(label)))

        title = self.query_one("#main-title", Label)
        name = next((n for k, n in CATEGORIES if k == category), category)
        title.update(f"{name}  ({len(items)})")

        if items:
            item_list.index = 0
            self.selected_index = 0
        else:
            self.selected_index = -1
        await self._refresh_detail()

    async def watch_selected_index(self, _idx: int) -> None:
        await self._refresh_detail()

    async def _refresh_detail(self) -> None:
        container = self.query_one("#detail-body", Container)
        items = items_for_report(self._report, self.selected_category)
        idx = self.selected_index
        await container.remove_children()
        if 0 <= idx < len(items):
            _label, payload, scope = items[idx]
            widgets = render_detail_widgets(self.selected_category, payload, scope)
            if widgets:
                await container.mount_all(widgets)
        else:
            await container.mount(Static("(no item selected)", classes="muted"))

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
