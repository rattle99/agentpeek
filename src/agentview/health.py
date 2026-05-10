from collections import defaultdict

from agentview.models import ScanResult, ScanWarning


def run_health_checks(result: ScanResult) -> list[ScanWarning]:
    issues: list[ScanWarning] = []
    issues.extend(_check_conflicting_keybindings(result))
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
