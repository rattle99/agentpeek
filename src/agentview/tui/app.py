from pathlib import Path
from typing import ClassVar

from textual.app import App
from textual.binding import Binding, BindingType

from agentview.scanner import scan
from agentview.tui.screens.main import MainScreen


class AgentViewApp(App[None]):
    CSS_PATH = "styles/app.tcss"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]
    TITLE = "agentview"

    def __init__(self, *, scan_root: Path | None, source_name: str | None) -> None:
        super().__init__()
        self._scan_root = scan_root
        self._source_name = source_name

    def on_mount(self) -> None:
        result = scan(self._scan_root, self._source_name)
        self.push_screen(MainScreen(result))
