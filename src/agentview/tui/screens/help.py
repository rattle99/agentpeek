from typing import ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static


class HelpScreen(ModalScreen[None]):
    """Modal listing every shown Binding from the active screen + app.

    Bindings are passed in by the caller (typically MainScreen.action_help)
    so the table reflects the live state of the running app.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q,question_mark,escape", "dismiss_modal", "Close"),
    ]

    def __init__(self, bindings: tuple[tuple[str, str], ...]) -> None:
        super().__init__()
        self._rows = bindings

    def compose(self) -> ComposeResult:
        with Container(id="help-card"):
            yield Static("Help — keys", id="help-title")
            yield DataTable[str](id="help-bindings", show_cursor=False)

    def on_mount(self) -> None:
        table = cast(
            "DataTable[str]",
            self.query_one("#help-bindings", DataTable),  # pyright: ignore[reportUnknownMemberType]
        )
        table.add_columns("Key", "Action")
        for key, desc in self._rows:
            table.add_row(key, desc)

    def action_dismiss_modal(self) -> None:
        self.dismiss()
