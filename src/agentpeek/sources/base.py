from pathlib import Path
from typing import Protocol, runtime_checkable

from agentpeek.models import ScanResult


@runtime_checkable
class Source(Protocol):
    name: str

    def detect(self, root: Path) -> bool: ...

    # Returns the conventional config-root path for this agent CLI
    # (e.g. `~/.claude` for LocalSource). Future Codex/Gemini/Cursor
    # sources will use this to surface their default scan target when
    # the user doesn't pass `--root`.
    def default_root(self) -> Path: ...

    def scan(self, root: Path) -> ScanResult: ...
