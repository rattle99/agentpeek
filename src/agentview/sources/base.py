from pathlib import Path
from typing import Protocol, runtime_checkable

from agentview.models import ScanResult


@runtime_checkable
class Source(Protocol):
    name: str

    def detect(self, root: Path) -> bool: ...

    def default_root(self) -> Path: ...

    def scan(self, root: Path) -> ScanResult: ...
