import dataclasses
from pathlib import Path

from agentview.models import ScanReport, ScanResult, ScanWarning
from agentview.tui.render import (
    COLOR_MUTED,
    COLOR_WARNING,
    redact,
    sidebar_count,
    warning_severity,
)


def test_warning_severity_mapping() -> None:
    assert warning_severity("plugin_state") == "error"
    assert warning_severity("source") == "error"
    assert warning_severity("orphan_hook") == "warning"
    assert warning_severity("conflicting_binding") == "info"
    assert warning_severity("scope_override_command") == "info"
    assert warning_severity("scope_layered_memory") == "info"
    # Unknown category defaults to warning — a safe middle ground that
    # surfaces the issue without screaming.
    assert warning_severity("nonexistent_category") == "warning"


def test_redact_short_value_unchanged() -> None:
    # Under 8 chars passes through so flags like "1" or "true" stay readable.
    assert redact("") == ""
    assert redact("x") == "x"
    assert redact("seven77") == "seven77"


def test_redact_long_value_masks_middle() -> None:
    # 8+ chars masks: keep first 4 and last 2 with an ellipsis between.
    assert redact("abcdefgh") == "abcd…gh"
    assert redact("sk-1234567890xyz") == "sk-1…yz"


def _make_report_with_warnings(n: int) -> ScanReport:
    user_root = Path("/user/.claude")
    project_root = Path("/proj/.claude")
    warnings = tuple(
        ScanWarning(path=None, category="plugin_state", reason=f"r{i}")
        for i in range(n)
    )
    user = dataclasses.replace(ScanResult.empty(root=user_root), warnings=warnings)
    project = ScanResult.empty(root=project_root)
    return ScanReport(user=user, project=project, project_root=project_root)


def _styles(label: object) -> list[str]:
    # rich.text.Text — return the list of style strings on its spans
    # (sorted in order of appearance) so tests can assert on color tokens.
    return [str(span.style) for span in label.spans]  # type: ignore[attr-defined]


def test_sidebar_count_zero_counts_styled_muted() -> None:
    # Empty fixture: settings count is 0 in both scopes → muted style.
    report = _make_report_with_warnings(0)
    label = sidebar_count(report, "settings")
    # Multi-scope mode renders "U:0 P:0" — every count span should be muted.
    assert COLOR_MUTED in " ".join(_styles(label))


def test_sidebar_count_warnings_count_styled_warning() -> None:
    # Three plugin_state warnings on the user side → user count should
    # render in the warning color, project count stays muted.
    report = _make_report_with_warnings(3)
    label = sidebar_count(report, "warnings")
    styles = " ".join(_styles(label))
    assert COLOR_WARNING in styles
    assert COLOR_MUTED in styles  # project zero count still dim
