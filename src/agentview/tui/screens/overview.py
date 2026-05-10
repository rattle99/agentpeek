from textual.app import ComposeResult
from textual.containers import Grid
from textual.screen import Screen
from textual.widgets import Footer, Header

from agentview.models import ScanResult
from agentview.tui.widgets.category_tile import CategoryTile


class OverviewScreen(Screen[None]):
    def __init__(self, result: ScanResult) -> None:
        super().__init__()
        self._result = result

    def compose(self) -> ComposeResult:
        yield Header()
        with Grid(id="tile-grid"):
            yield CategoryTile("Settings", self._settings_summary())
            yield CategoryTile("Hooks", f"{len(self._result.hooks)} hook(s)")
            yield CategoryTile("Slash commands", f"{len(self._result.commands)}")
            yield CategoryTile("Plugins", f"{len(self._result.plugins)}")
            yield CategoryTile("Memory", f"{len(self._result.memory)} file(s)")
            yield CategoryTile("Keybindings", self._keybindings_summary())
            yield CategoryTile("MCP servers", f"{len(self._result.mcp)}")
            yield CategoryTile(
                "Scan warnings",
                f"{len(self._result.warnings)}",
                variant="warn" if self._result.warnings else "ok",
            )
        yield Footer()

    def _settings_summary(self) -> str:
        s = self._result.settings
        if s is None:
            return "no settings.json"
        parts: list[str] = []
        if s.model:
            parts.append(f"model: {s.model}")
        if s.theme:
            parts.append(f"theme: {s.theme}")
        parts.append(f"{len(s.permissions_allow)} allow rule(s)")
        return "\n".join(parts)

    def _keybindings_summary(self) -> str:
        kb = self._result.keybindings
        if kb is None:
            return "no keybindings.json"
        return f"{len(kb.entries)} binding(s)"
