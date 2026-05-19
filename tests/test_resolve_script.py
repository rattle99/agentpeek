from pathlib import Path

import pytest

from agentpeek.sources.local import _resolve_script


def test_tilde_path_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".claude"
    path, dynamic = _resolve_script("~/.claude/hooks/foo.sh", root)
    assert path == root / "hooks/foo.sh"
    assert dynamic is False


def test_tilde_with_leading_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".claude"
    path, dynamic = _resolve_script("bash ~/.claude/hooks/foo.sh", root)
    assert path == root / "hooks/foo.sh"
    assert dynamic is False


def test_home_env_expansion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".claude"
    path, dynamic = _resolve_script("$HOME/.claude/hooks/foo.sh", root)
    assert path == root / "hooks/foo.sh"
    assert dynamic is False


def test_home_brace_expansion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".claude"
    path, dynamic = _resolve_script("${HOME}/.claude/hooks/foo.sh", root)
    assert path == root / "hooks/foo.sh"
    assert dynamic is False


def test_project_dir_env_with_default() -> None:
    root = Path("/tmp/project/.claude")
    path, dynamic = _resolve_script(
        "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/markdownlint.sh", root
    )
    assert path == root / "hooks/markdownlint.sh"
    assert dynamic is True


def test_project_dir_env_no_default() -> None:
    root = Path("/tmp/project/.claude")
    path, dynamic = _resolve_script(
        "${CLAUDE_PROJECT_DIR}/.claude/hooks/foo.sh", root
    )
    assert path == root / "hooks/foo.sh"
    assert dynamic is True


def test_absolute_path_under_root() -> None:
    root = Path("/Users/aurkomitra/.claude")
    path, dynamic = _resolve_script(
        "/Users/aurkomitra/.claude/hooks/foo.sh", root
    )
    assert path == Path("/Users/aurkomitra/.claude/hooks/foo.sh")
    assert dynamic is False


def test_quoted_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".claude"
    path, dynamic = _resolve_script('"bash ~/.claude/hooks/foo.sh"', root)
    assert path == root / "hooks/foo.sh"
    assert dynamic is False


def test_unresolvable_command_returns_none() -> None:
    root = Path("/tmp/.claude")
    path, dynamic = _resolve_script("echo hello world", root)
    assert path is None
    assert dynamic is False


def test_unbalanced_quote_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".claude"
    # shlex.split raises ValueError on unbalanced quotes — we now return
    # None so the caller can warn rather than substring-matching against
    # mangled tokens.
    assert _resolve_script("bash 'unclosed ~/.claude/hooks/foo.sh", root) is None
