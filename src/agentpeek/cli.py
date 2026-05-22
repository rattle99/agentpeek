import argparse
import importlib.metadata
import sys
from pathlib import Path

from agentpeek.logging_setup import configure_logging
from agentpeek.sources.local import LocalSource


def _existing_dir(value: str) -> Path:
    p = Path(value)
    if not p.is_dir():
        raise argparse.ArgumentTypeError(f"{value!r} is not a directory")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentpeek",
        description="TUI inspector for agent CLI configuration directories.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"agentpeek {importlib.metadata.version('agentpeek')}",
    )
    parser.add_argument(
        "--root",
        type=_existing_dir,
        default=None,
        help="Config root to scan. Default: ~/.claude.",
    )
    # Reserved for v2.x when additional Source implementations (Codex,
    # Gemini, Cursor) land. Hidden today because only "local" exists.
    parser.add_argument(
        "--source", default=None, choices=["local"], help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Disable write actions. By default the TUI exposes update / "
        "enable / disable / uninstall / marketplace-refresh bindings that "
        "shell out to the `claude plugin` CLI.",
    )
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    if args.root is not None and not LocalSource().detect(args.root):
        print(
            f"agentpeek: no Claude Code config found in {str(args.root)!r} "
            "(pass a .claude/ directory)",
            file=sys.stderr,
        )
        return 2

    # Defer Textual import so --version and --help are fast and don't drag the
    # TUI into module-load time when only the CLI surface is exercised.
    from agentpeek.tui.app import AgentViewApp  # noqa: PLC0415

    AgentViewApp(
        scan_root=args.root,
        source_name=args.source,
        actions=not args.read_only,
    ).run()
    return 0
