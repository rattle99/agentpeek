import dataclasses
from pathlib import Path

from agentview.health import run_health_checks
from agentview.models import ScanReport, ScanResult
from agentview.sources.base import Source
from agentview.sources.local import LocalSource

ALL_SOURCES: tuple[Source, ...] = (LocalSource(),)
USER_CLAUDE_DIR = Path.home() / ".claude"


def find_project_root(start: Path) -> Path | None:
    """Walk up from `start` looking for a `.claude/` directory, stopping at $HOME.

    Stopping at $HOME is deliberate: it prevents `$HOME/.claude/` (the
    user-level config) from being misidentified as a project-level root when
    agentview is run from anywhere inside the home directory.
    """
    home = Path.home().resolve()
    try:
        current = start.resolve()
    except OSError:
        return None
    while current not in (home, current.parent):
        candidate = current / ".claude"
        if candidate.is_dir():
            return candidate
        current = current.parent
    return None


def scan(root: Path | None = None, source_name: str | None = None) -> ScanReport:
    if root is not None:
        # Explicit override — single scope. Treat as project unless the path
        # resolves to the user-level dir, in which case keep it as user.
        result = _run(root, source_name)
        if root.resolve() == USER_CLAUDE_DIR.resolve():
            return ScanReport(user=result, project=None, project_root=None)
        return ScanReport(user=None, project=result, project_root=root)

    user_result: ScanResult | None = None
    if USER_CLAUDE_DIR.is_dir():
        user_result = _run(USER_CLAUDE_DIR, source_name)

    project_root = find_project_root(Path.cwd())
    project_result: ScanResult | None = None
    if project_root is not None:
        project_result = _run(project_root, source_name)

    return ScanReport(
        user=user_result, project=project_result, project_root=project_root
    )


def _run(target: Path, source_name: str | None) -> ScanResult:
    raw = _scan_raw(target, source_name)
    issues = run_health_checks(raw)
    if issues:
        return dataclasses.replace(raw, warnings=raw.warnings + tuple(issues))
    return raw


def _scan_raw(root: Path, source_name: str | None) -> ScanResult:
    if source_name is not None:
        for s in ALL_SOURCES:
            if s.name == source_name:
                return s.scan(root)
        return ScanResult.empty(reason=f"unknown source: {source_name!r}")

    for s in ALL_SOURCES:
        if s.detect(root):
            return s.scan(root)
    return ScanResult.empty(root=root, reason=f"no known source detected at {root}")
