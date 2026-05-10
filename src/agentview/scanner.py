from pathlib import Path

from agentview.models import ScanResult
from agentview.sources.base import Source
from agentview.sources.local import LocalSource

ALL_SOURCES: tuple[Source, ...] = (LocalSource(),)


def scan(root: Path | None = None, source_name: str | None = None) -> ScanResult:
    if source_name is not None:
        for s in ALL_SOURCES:
            if s.name == source_name:
                target = root or s.default_root()
                return s.scan(target)
        return ScanResult.empty(reason=f"unknown source: {source_name!r}")

    if root is not None:
        for s in ALL_SOURCES:
            if s.detect(root):
                return s.scan(root)
        return ScanResult.empty(root=root, reason=f"no known source detected at {root}")

    default_source = ALL_SOURCES[0]
    return default_source.scan(default_source.default_root())
