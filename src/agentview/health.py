from collections import defaultdict

from agentview.models import ScanReport, ScanResult, ScanWarning


def run_health_checks(result: ScanResult) -> list[ScanWarning]:
    issues: list[ScanWarning] = []
    issues.extend(_check_conflicting_keybindings(result))
    issues.extend(_check_orphan_hooks(result))
    issues.extend(_check_plugin_state(result))
    return issues


def _check_conflicting_keybindings(result: ScanResult) -> list[ScanWarning]:
    if result.keybindings is None:
        return []
    seen: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
    for entry in result.keybindings.entries:
        seen[(entry.context, entry.key)].append(entry.action)
    issues: list[ScanWarning] = []
    for (context, key), actions in sorted(seen.items()):
        unique = sorted(set(actions))
        if len(unique) > 1:
            issues.append(
                ScanWarning(
                    path=result.keybindings.path,
                    category="conflicting_binding",
                    reason=(
                        f"context {context!r} key {key!r} maps to multiple "
                        f"actions: {unique}"
                    ),
                )
            )
    return issues


def _check_orphan_hooks(result: ScanResult) -> list[ScanWarning]:
    hooks_dir = result.root / "hooks"
    if not hooks_dir.is_dir():
        return []
    referenced = {
        h.referenced_script.resolve()
        for h in result.hooks
        if h.referenced_script is not None
    }
    issues: list[ScanWarning] = []
    for f in sorted(hooks_dir.iterdir()):
        if f.is_file() and f.resolve() not in referenced:
            issues.append(
                ScanWarning(
                    path=f,
                    category="orphan_hook",
                    reason=(
                        f"script {f.name} is not referenced from any settings hook"
                    ),
                )
            )
    return issues


def _check_plugin_state(result: ScanResult) -> list[ScanWarning]:
    issues: list[ScanWarning] = []
    for plugin in result.plugins:
        if plugin.enabled and not plugin.installations:
            issues.append(
                ScanWarning(
                    path=None,
                    category="plugin_state",
                    reason=(
                        f"plugin {plugin.qualified_id} is enabled but has "
                        "no installations on disk"
                    ),
                )
            )
    return issues


def run_cross_scope_checks(report: ScanReport) -> list[ScanWarning]:
    """Cross-scope diagnostics: comparing user-level and project-level scans
    for overlaps, overrides, and layered configuration."""
    if report.user is None or report.project is None:
        return []
    issues: list[ScanWarning] = []
    issues.extend(_check_scope_override_command(report))
    issues.extend(_check_scope_override_plugin(report))
    issues.extend(_check_scope_layered_memory(report))
    return issues


def _check_scope_override_command(report: ScanReport) -> list[ScanWarning]:
    assert report.user is not None and report.project is not None
    user_names = {c.name for c in report.user.commands}
    project_names = {c.name for c in report.project.commands}
    return [
        ScanWarning(
            path=None,
            category="scope_override_command",
            reason=(
                f"slash command /{name} exists in both user and project scope; "
                "project version takes precedence"
            ),
        )
        for name in sorted(user_names & project_names)
    ]


def _check_scope_override_plugin(report: ScanReport) -> list[ScanWarning]:
    assert report.user is not None and report.project is not None
    user_ids = {p.qualified_id for p in report.user.plugins}
    project_ids = {p.qualified_id for p in report.project.plugins}
    return [
        ScanWarning(
            path=None,
            category="scope_override_plugin",
            reason=(f"plugin {qid} has installations in both user and project scope"),
        )
        for qid in sorted(user_ids & project_ids)
    ]


def _check_scope_layered_memory(report: ScanReport) -> list[ScanWarning]:
    assert report.user is not None and report.project is not None
    user_claude = any(m.kind == "claude_md" for m in report.user.memory)
    project_claude = any(m.kind == "claude_md" for m in report.project.memory)
    if not (user_claude and project_claude):
        return []
    return [
        ScanWarning(
            path=None,
            category="scope_layered_memory",
            reason=(
                "CLAUDE.md exists at both user and project scope; "
                "project memory layers on top of user memory"
            ),
        )
    ]
