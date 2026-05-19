from importlib.metadata import version

import pytest

from agentpeek.cli import main


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert version("agentpeek") in captured.out


def test_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "usage: agentpeek" in captured.out


def test_root_nonexistent_dir_exits_nonzero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--root", "/this/path/does/not/exist"])
    # argparse exits 2 on argument errors.
    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert "not a directory" in captured.err


def test_source_unknown_exits_nonzero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--source", "codex"])
    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert "invalid choice" in captured.err


def test_root_directory_without_claude_config_exits_nonzero(
    tmp_path: pytest.TempPathFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # An existing directory that has none of settings.json / plugins/ /
    # hooks/ / commands/ / CLAUDE.md / keybindings.json should be
    # refused with a clear message rather than opening the TUI to an
    # empty scan.
    plain = tmp_path  # type: ignore[assignment]
    assert main([f"--root={plain}"]) == 2
    captured = capsys.readouterr()
    assert "no Claude Code config" in captured.err
