from pathlib import Path
from typing import ClassVar

from textual.app import App
from textual.binding import Binding, BindingType

from agentview.scanner import scan
from agentview.tui.render import scope_path, scope_summary
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
        report = scan(self._scan_root, self._source_name)
        explicit = self._scan_root is not None
        summary = scope_summary(report, explicit_root=explicit)
        path = scope_path(report)
        self.sub_title = f"{summary} · {path}" if path else summary
        self.push_screen(MainScreen(report, explicit_root=explicit))
