from pathlib import Path

from agentpeek.health import _check_plugin_state
from agentpeek.models import Plugin, PluginInstallation, ScanResult


def _result(plugins: tuple[Plugin, ...]) -> ScanResult:
    return ScanResult(
        source="local",
        root=Path("/tmp/.claude"),
        settings=None,
        hooks=(),
        commands=(),
        plugins=plugins,
        memory=(),
        keybindings=None,
        mcp=(),
        warnings=(),
    )


def _plugin(
    qid: str, *, enabled: bool, installations: tuple[PluginInstallation, ...]
) -> Plugin:
    return Plugin(
        id=qid.split("@")[0],
        marketplace=qid.split("@")[1],
        qualified_id=qid,
        enabled=enabled,
        installations=installations,
    )


def _install(path: Path) -> PluginInstallation:
    return PluginInstallation(
        scope="managed",
        install_path=path,
        version="1.0.0",
        installed_at="2026-01-01T00:00:00Z",
        last_updated="2026-01-01T00:00:00Z",
        git_commit_sha=None,
        project_path=None,
    )


def test_enabled_but_no_installations_flagged() -> None:
    issues = _check_plugin_state(
        _result((_plugin("foo@m", enabled=True, installations=()),))
    )
    assert len(issues) == 1
    assert "no installations" in issues[0].reason


def test_disabled_with_installations_flagged(tmp_path: Path) -> None:
    installed = tmp_path / "installed-plugin"
    installed.mkdir()
    issues = _check_plugin_state(
        _result(
            (
                _plugin(
                    "foo@m",
                    enabled=False,
                    installations=(_install(installed),),
                ),
            )
        )
    )
    assert len(issues) == 1
    assert "not enabled" in issues[0].reason


def test_missing_install_path_flagged() -> None:
    issues = _check_plugin_state(
        _result(
            (
                _plugin(
                    "foo@m",
                    enabled=True,
                    installations=(_install(Path("/does/not/exist")),),
                ),
            )
        )
    )
    assert len(issues) == 1
    assert "missing install path" in issues[0].reason


def test_healthy_plugin_no_warnings(tmp_path: Path) -> None:
    installed = tmp_path / "ok-plugin"
    installed.mkdir()
    issues = _check_plugin_state(
        _result(
            (
                _plugin(
                    "foo@m",
                    enabled=True,
                    installations=(_install(installed),),
                ),
            )
        )
    )
    assert issues == []
