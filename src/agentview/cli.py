import argparse
import importlib.metadata
from pathlib import Path

from agentview.logging_setup import configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentview",
        description="TUI inspector for agent CLI configuration directories.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"agentview {importlib.metadata.version('agentview')}",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Config root to scan. Default: ~/.claude.",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Force a source by name. Default: auto-detect.",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    # Defer Textual import so --version and --help are fast and don't drag the
    # TUI into module-load time when only the CLI surface is exercised.
    from agentview.tui.app import AgentViewApp  # noqa: PLC0415

    AgentViewApp(scan_root=args.root, source_name=args.source).run()
    return 0
