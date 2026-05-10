from rich.console import RenderableType
from textual.widgets import Static


class CategoryTile(Static):
    def __init__(self, title: str, summary: str, *, variant: str = "ok") -> None:
        super().__init__()
        self._title = title
        self._summary = summary
        self.add_class(f"tile-{variant}")

    def render(self) -> RenderableType:
        return f"[b]{self._title}[/b]\n{self._summary}"
