from pathlib import Path
from typing import ClassVar

from textual.app import App
from textual.binding import Binding, BindingType

from agentpeek.models import ScanReport
from agentpeek.scanner import scan
from agentpeek.tui.render import scope_path, scope_summary
from agentpeek.tui.screens.main import MainScreen


class AgentViewApp(App[None]):
    CSS_PATH = "styles/app.tcss"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]
    TITLE = "agentpeek"

    def __init__(
        self,
        *,
        scan_root: Path | None,
        source_name: str | None,
        actions: bool = True,
    ) -> None:
        super().__init__()
        self._scan_root = scan_root
        self._source_name = source_name
        self._actions = actions

    def rescan(self) -> ScanReport:
        """Re-run the scanner with the cached args and update the subtitle.

        Returns the new report. Callers (typically `MainScreen.action_refresh`)
        re-seat their own report reference and rebuild any cached UI state.
        """
        report = scan(self._scan_root, self._source_name)
        explicit = self._scan_root is not None
        summary = scope_summary(report, explicit_root=explicit)
        path = scope_path(report)
        self.sub_title = f"{summary} · {path}" if path else summary
        return report

    def on_mount(self) -> None:
        report = self.rescan()
        self.push_screen(
            MainScreen(
                report,
                explicit_root=self._scan_root is not None,
                actions=self._actions,
            )
        )
