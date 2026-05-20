import dataclasses
from pathlib import Path
from typing import cast

from agentpeek.health import run_cross_scope_checks, run_health_checks
from agentpeek.models import (
    Plugin,
    PluginInstallation,
    ScanReport,
    ScanResult,
    ScanWarning,
)
from agentpeek.parsers import load_json
from agentpeek.sources.base import Source
from agentpeek.sources.local import LocalSource

ALL_SOURCES: tuple[Source, ...] = (LocalSource(),)
USER_CLAUDE_DIR = Path.home() / ".claude"


def find_project_root(start: Path) -> Path | None:
    """Walk up from `start` looking for a `.claude/` directory, stopping at $HOME.

    Stopping at $HOME is deliberate: it prevents `$HOME/.claude/` (the
    user-level config) from being misidentified as a project-level root when
    agentpeek is run from anywhere inside the home directory.
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
        result = _attach_health_warnings(_scan_raw(root, source_name))
        if root.resolve() == USER_CLAUDE_DIR.resolve():
            return ScanReport(user=result, project=None, project_root=None)
        return ScanReport(user=None, project=result, project_root=root)

    # Scan both scopes raw, then redistribute project-scoped installations
    # from the user registry into project.plugins, THEN run health checks —
    # otherwise a plugin enabled at project scope but installed via the
    # user-level registry generates a stale "installed but not enabled"
    # warning at user scope before it's moved.
    user_raw: ScanResult | None = (
        _scan_raw(USER_CLAUDE_DIR, source_name) if USER_CLAUDE_DIR.is_dir() else None
    )
    project_root = find_project_root(Path.cwd())
    project_raw: ScanResult | None = (
        _scan_raw(project_root, source_name) if project_root is not None else None
    )

    report = ScanReport(
        user=user_raw, project=project_raw, project_root=project_root
    )
    report = redistribute_plugins(report)

    if report.user is not None:
        report = dataclasses.replace(
            report, user=_attach_health_warnings(report.user)
        )
    if report.project is not None:
        report = dataclasses.replace(
            report, project=_attach_health_warnings(report.project)
        )
    return _attach_cross_scope_warnings(report)


def _attach_cross_scope_warnings(report: ScanReport) -> ScanReport:
    issues = run_cross_scope_checks(report)
    if not issues or report.project is None:
        return report
    new_project = dataclasses.replace(
        report.project, warnings=report.project.warnings + tuple(issues)
    )
    return dataclasses.replace(report, project=new_project)


def redistribute_plugins(report: ScanReport) -> ScanReport:
    """Move user-registry plugins with project-scoped installations into the
    project scope, recomputing the enabled flag against project settings.

    Plugins live in the user-level registry (`~/.claude/plugins/installed_plugins.json`)
    even when their installations are scoped to a particular project. When
    agentpeek discovers a project root, those installations belong logically
    in the project scope.
    """
    if report.user is None or report.project_root is None or report.project is None:
        return report

    project_dir = report.project_root.parent.resolve()

    user_kept: list[Plugin] = []
    project_added: list[Plugin] = []

    for plugin in report.user.plugins:
        user_inst, project_inst = _split_installations(
            plugin.installations, project_dir
        )
        if user_inst:
            user_kept.append(
                dataclasses.replace(plugin, installations=tuple(user_inst))
            )
        if project_inst:
            project_added.append(
                dataclasses.replace(plugin, installations=tuple(project_inst))
            )

    if not project_added:
        return report

    # Recompute `enabled` for plugins moved into the project scope using the
    # project's own enabledPlugins maps (settings.json + remote-settings.json).
    extra_warnings: list[ScanWarning] = []
    project_enabled = _project_enabled_plugins(report.project_root, extra_warnings)
    project_added_finalized = tuple(
        dataclasses.replace(p, enabled=p.qualified_id in project_enabled)
        for p in project_added
    )

    new_user = dataclasses.replace(report.user, plugins=tuple(user_kept))
    new_project = dataclasses.replace(
        report.project,
        plugins=report.project.plugins + project_added_finalized,
        warnings=report.project.warnings + tuple(extra_warnings),
    )
    return dataclasses.replace(report, user=new_user, project=new_project)


def _split_installations(
    installations: tuple[PluginInstallation, ...], project_dir: Path
) -> tuple[list[PluginInstallation], list[PluginInstallation]]:
    # Match on `project_path` regardless of `scope`: real installations use
    # `project`, `local`, and other strings interchangeably for project-scoped
    # installs. The reliable signal is the projectPath field.
    user_inst: list[PluginInstallation] = []
    project_inst: list[PluginInstallation] = []
    for inst in installations:
        if inst.project_path is not None and inst.project_path.resolve() == project_dir:
            project_inst.append(inst)
        else:
            user_inst.append(inst)
    return user_inst, project_inst


def _project_enabled_plugins(
    project_root: Path, warnings: list[ScanWarning]
) -> set[str]:
    enabled: set[str] = set()
    for filename in ("settings.json", "settings.local.json", "remote-settings.json"):
        path = project_root / filename
        if not path.exists():
            continue
        data, warning = load_json(path, category="plugins")
        if warning is not None:
            warnings.append(warning)
        if isinstance(data, dict):
            ep = cast("dict[str, object]", data).get("enabledPlugins")
            if isinstance(ep, dict):
                ep_d = cast("dict[str, object]", ep)
                for k, v in ep_d.items():
                    if bool(v):
                        enabled.add(str(k))
    return enabled


def _attach_health_warnings(raw: ScanResult) -> ScanResult:
    issues = run_health_checks(raw)
    if not issues:
        return raw
    return dataclasses.replace(raw, warnings=raw.warnings + tuple(issues))


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
