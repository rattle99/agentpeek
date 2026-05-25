# agentpeek

[![PyPI](https://img.shields.io/pypi/v/agentpeek?style=for-the-badge&logo=pypi&logoColor=white&color=e68e6e)](https://pypi.org/project/agentpeek/)
[![Python](https://img.shields.io/pypi/pyversions/agentpeek?style=for-the-badge&logo=python&logoColor=white&color=e68e6e)](https://pypi.org/project/agentpeek/)
[![License](https://img.shields.io/badge/license-GPL--3.0-e68e6e?style=for-the-badge)](LICENSE)

A TUI for inspecting agent CLI configurations: what's installed, what's enabled, what's broken.

agentpeek scans your Claude Code config (`~/.claude/` and any project-level `.claude/` directory it finds by walking up from `cwd`) and surfaces every loose file the agent depends on: settings, hooks, slash commands, memory files, MCP servers, user/project subagents, path-scoped rules, installed plugins (with their full contents: manifest, skills, agents, commands, hooks, MCPs), and any health warnings detected during the scan.

## Install

    pipx install agentpeek

Then:

    agentpeek                      # scan ~/.claude and the nearest project .claude
    agentpeek --root /path/to/dir  # scan a specific directory
    agentpeek --version

Requires Python ≥ 3.11. Linux and macOS only.

## What you get

- **Three-zone TUI**: sidebar of 11 categories on the left, items list in the middle, detail pane on the right. Drill-down on every category.
- **Multi-scope**: user-scoped (`~/.claude/`) and project-scoped (`.claude/` found by walking up from cwd) configurations shown side by side with `[U]` / `[P]` badges. Health checks compare the two and flag conflicts.
- **Subagents**: user and project agents under `agents/*.md` are scanned recursively, with full frontmatter (tools, model, permission mode, memory scope, color, and the rest) shown in the detail card.
- **Path-scoped rules**: `.claude/rules/*.md` files at user and project scope are listed with their `paths:` globs (or marked "always loaded" when the field is absent), so you can answer "why is Claude doing X here?" by category instead of by guessing.
- **Plugin contents**: every installed plugin is opened up: manifest, skills, agents, slash commands, hooks, MCP servers, and the marketplace's auto-update state. Plugin-contributed commands / hooks / MCPs also merge into the top-level categories with a `[plug:<id>]` provenance tag.
- **Memory layer**: `CLAUDE.md`, `CLAUDE.local.md` (project-root personal overrides), and subagent persistent memory directories (`agent-memory/` and `agent-memory-local/`) are all surfaced. CLAUDE.md `@path/to/file` imports are resolved recursively (up to 5 hops, cycles detected) so you see what Claude actually loads.
- **Auto-memory**: Claude Code project memories under `~/.claude/projects/<encoded>/memory/` are scanned and labelled with their actual project path (resolved from session logs, not the lossy directory encoding).
- **`~/.claude.json`**: user-scope MCP servers, per-project trust state (allowed tools, enabled / disabled `.mcp.json` servers), and OAuth session presence (display-only - the token value is never read or shown). Folded into the MCP and Settings cards.
- **Health checks**: orphan hook scripts, missing plugin install paths, conflicting key bindings, scope override conflicts, cross-scope layered memory, hooks referencing dynamic env vars (`${CLAUDE_PROJECT_DIR}`, `$HOME`, ...) flagged so you know which won't resolve at scan time, and more. Severity-coloured warning cards in the detail pane.
- **Write actions**: update, enable/disable, uninstall a plugin or refresh a marketplace without leaving the TUI. Calls shell out to `claude plugin ...`; agentpeek never edits Claude state files directly. Re-scan is automatic after every action. Pass `--read-only` to disable.

## Keybindings

| Key | Action |
|---|---|
| `↑` / `↓` | Move within the focused list |
| `Tab` / `Shift+Tab` | Move focus between sidebar / items / detail |
| `Enter` | Drill into the highlighted item; on a Skills or Agents row inside a plugin, opens a detail modal |
| `r` | Re-scan disk |
| `o` | Open the highlighted item's file in `$EDITOR` (TUI suspends + resumes) |
| `y` | Yank the highlighted item's path to the clipboard (OSC 52) |
| `b` | Yank the rendered body. Works on memory entries, slash commands, skills, agents, hooks |
| `u` | Update the highlighted plugin |
| `U` | Update all installed plugins (progress in the title bar) |
| `t` | Toggle enable / disable for the highlighted plugin |
| `x` | Uninstall the highlighted plugin (confirms first) |
| `M` | Refresh the highlighted marketplace |
| `/` | Show + focus the filter input (live-filters the items list) |
| `Esc` | Clear filter and refocus items |
| `?` | Help overlay listing every binding |
| `q` | Quit |

The filter shows `(filtered: N / M)` in the title while active. Press Enter inside the filter to keep the filter and jump back to the items list; press Esc to clear.

## Theming

Built on Textual: the theme follows `Ctrl+P` (palette) and reflows colours across cards, borders, badges, and severity indicators. Default theme is `textual-dark`.

## Supported agent CLIs

- **Claude Code**: full support today.
- Support for other agents (Codex, Gemini CLI, Cursor) coming.

## License

GPL-3.0-only. See [LICENSE](LICENSE).
