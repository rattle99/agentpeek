from collections import defaultdict

from agentview.models import ScanResult, ScanWarning


def run_health_checks(result: ScanResult) -> list[ScanWarning]:
    issues: list[ScanWarning] = []
    issues.extend(_check_conflicting_keybindings(result))
    issues.extend(_check_orphan_hooks(result))
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
