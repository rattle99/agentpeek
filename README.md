# agentview

A TUI inspector for agent CLI configuration directories. v0.1.0 surfaces a one-screen overview of your Claude Code (`~/.claude/`) install: settings, hooks, slash commands, plugins, memory files, keybindings, MCP servers, and any parse warnings encountered during the scan.

## Install

    pipx install agentview

## Run

    agentview                      # scan ~/.claude
    agentview --root /path/to/dir  # scan a specific directory
    agentview --version
    agentview --help

## Status

Pre-alpha. v0.1.0 is read-only and shows aggregate counts only. Drill-down lands in v0.2; health checks in v0.3; project-level scanning + inline filter in v0.4. Future versions extend support to other agent CLIs (Codex, Gemini, Cursor).

## License

GPL-3.0-or-later. See `LICENSE`.
