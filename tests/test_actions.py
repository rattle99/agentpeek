import subprocess
from unittest.mock import MagicMock, patch

import pytest

from agentpeek.actions.runner import (
    ActionResult,
    run_marketplace_update,
    run_plugin,
)


def _fake_completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


@pytest.mark.parametrize(
    "verb",
    ["enable", "disable", "update", "uninstall", "install"],
)
def test_run_plugin_passes_correct_argv(verb: str) -> None:
    with patch("agentpeek.actions.runner.subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed(0, "done", "")
        result = run_plugin(verb, "alpha@market", scope="user")  # type: ignore[arg-type]
    assert mock_run.called
    args = mock_run.call_args.args[0]
    assert args == ["claude", "plugin", verb, "alpha@market", "--scope", "user"]
    assert result.ok is True
    assert result.verb == verb
    assert result.target == "alpha@market"
    assert result.scope == "user"


def test_run_plugin_project_scope() -> None:
    with patch("agentpeek.actions.runner.subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed(0)
        run_plugin("update", "beta@market", scope="project")
    args = mock_run.call_args.args[0]
    assert "--scope" in args
    assert args[args.index("--scope") + 1] == "project"


def test_run_plugin_failure_returncode_propagates() -> None:
    with patch("agentpeek.actions.runner.subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed(
            returncode=2, stdout="", stderr="missing dependency"
        )
        result = run_plugin("update", "alpha@m", scope="user")
    assert result.ok is False
    assert result.returncode == 2
    assert "missing dependency" in result.stderr


def test_run_marketplace_update_single_target() -> None:
    with patch("agentpeek.actions.runner.subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed(0)
        result = run_marketplace_update("ds-ai-setu")
    args = mock_run.call_args.args[0]
    assert args == ["claude", "plugin", "marketplace", "update", "ds-ai-setu"]
    assert result.verb == "marketplace update"
    assert result.target == "ds-ai-setu"
    assert result.scope is None


def test_run_marketplace_update_all() -> None:
    with patch("agentpeek.actions.runner.subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed(0)
        result = run_marketplace_update(None)
    args = mock_run.call_args.args[0]
    # No trailing marketplace name → refresh-all form.
    assert args == ["claude", "plugin", "marketplace", "update"]
    assert result.target == "(all)"


def test_run_plugin_handles_missing_cli() -> None:
    with patch("agentpeek.actions.runner.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError
        result = run_plugin("enable", "alpha@m", scope="user")
    assert result.ok is False
    assert result.returncode == -1
    assert "claude" in result.stderr.lower()


def test_run_plugin_handles_timeout() -> None:
    with patch("agentpeek.actions.runner.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["claude"], timeout=60, output=b"partial output"
        )
        result = run_plugin("update", "alpha@m", scope="user")
    assert result.ok is False
    assert result.returncode == -1
    assert "timed out" in result.stderr
    assert "partial output" in result.stdout


def test_action_result_message_uses_last_line() -> None:
    result = ActionResult(
        ok=True,
        verb="update",
        target="alpha@m",
        scope="user",
        returncode=0,
        stdout="line1\nfinal line",
        stderr="",
        elapsed_ms=100,
    )
    assert result.message == "final line"


def test_action_result_message_falls_back() -> None:
    result = ActionResult(
        ok=True,
        verb="update",
        target="alpha@m",
        scope="user",
        returncode=0,
        stdout="",
        stderr="",
        elapsed_ms=10,
    )
    assert result.message == "ok"
