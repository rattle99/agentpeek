"""Shell-out runner for `claude plugin …` write operations.

agentpeek does not edit Claude state files directly. Every write goes
through the `claude` CLI, which handles dependency validation, version
tracking, and cache invariants. See docs.claude.com plugin reference.
"""

import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Literal

PluginVerb = Literal["enable", "disable", "update", "uninstall", "install"]
# `managed` is accepted by `claude plugin update` for enterprise-pushed
# plugins; enable/disable/uninstall do not accept it. If a managed-scope
# plugin is targeted with a non-update verb, the CLI surfaces a clear error
# in the result modal.
Scope = Literal["user", "project", "local", "managed"]

_TIMEOUT_SECONDS = 60


@dataclass(frozen=True, slots=True)
class ActionResult:
    ok: bool
    verb: str
    target: str
    scope: str | None
    returncode: int
    stdout: str
    stderr: str
    elapsed_ms: int

    @property
    def message(self) -> str:
        """One-line summary suitable for a toast notification."""
        merged = (self.stdout + self.stderr).strip().splitlines()
        return merged[-1] if merged else ("ok" if self.ok else "failed")


def claude_cli_available() -> bool:
    return shutil.which("claude") is not None


def run_plugin(verb: PluginVerb, qualified_id: str, *, scope: Scope) -> ActionResult:
    cmd = ["claude", "plugin", verb, qualified_id, "--scope", scope]
    return _run(cmd, verb=verb, target=qualified_id, scope=scope)


def run_marketplace_update(name: str | None) -> ActionResult:
    """Refresh one marketplace (or all if `name` is None)."""
    cmd = ["claude", "plugin", "marketplace", "update"]
    if name is not None:
        cmd.append(name)
    return _run(cmd, verb="marketplace update", target=name or "(all)", scope=None)


def _run(
    cmd: list[str], *, verb: str, target: str, scope: str | None
) -> ActionResult:
    start = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603 - cmd is built from a fixed allowlist
            cmd,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ActionResult(
            ok=proc.returncode == 0,
            verb=verb,
            target=target,
            scope=scope,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            elapsed_ms=elapsed_ms,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        partial = exc.stdout
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        return ActionResult(
            ok=False,
            verb=verb,
            target=target,
            scope=scope,
            returncode=-1,
            stdout=partial or "",
            stderr=f"timed out after {_TIMEOUT_SECONDS}s",
            elapsed_ms=elapsed_ms,
        )
    except FileNotFoundError:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ActionResult(
            ok=False,
            verb=verb,
            target=target,
            scope=scope,
            returncode=-1,
            stdout="",
            stderr="`claude` CLI not found on PATH",
            elapsed_ms=elapsed_ms,
        )
