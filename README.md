# agentpeek

A TUI for inspecting agent CLI configurations — what's installed, what's enabled, what's broken.

agentpeek scans your Claude Code config (`~/.claude/` and any project-level `.claude/` directory it finds by walking up from `cwd`) and surfaces every loose file the agent depends on: settings, hooks, slash commands, memory files, MCP servers, installed plugins (with their full contents — manifest, skills, agents, commands, hooks, MCPs), and any health warnings detected during the scan.

## Install

    pipx install agentpeek

Then:

    agentpeek                      # scan ~/.claude and the nearest project .claude
    agentpeek --root /path/to/dir  # scan a specific directory
    agentpeek --version

Requires Python ≥ 3.11. Linux and macOS only.

## What you get

- **Three-zone TUI** — sidebar of 9 categories on the left, items list in the middle, detail pane on the right. Drill-down on every category.
- **Multi-scope** — user-scoped (`~/.claude/`) and project-scoped (`.claude/` found by walking up from cwd) configurations shown side by side with `[U]` / `[P]` badges. Health checks compare the two and flag conflicts.
- **Plugin contents** — every installed plugin is opened up: manifest, skills, agents, slash commands, hooks, MCP servers. Plugin-contributed commands / hooks / MCPs also merge into the top-level categories with a `[plug:<id>]` provenance tag.
- **Auto-memory** — Claude Code project memories (under `~/.claude/projects/<encoded>/memory/`) are scanned and labelled with their actual project path (resolved from session logs, not the lossy directory encoding).
- **Health checks** — orphan hook scripts, missing plugin install paths, conflicting key bindings, scope override conflicts, cross-scope layered memory, and more. Severity-coloured warning cards in the detail pane.

## Keybindings

| Key | Action |
|---|---|
| `↑` / `↓` | Move within the focused list |
| `Tab` / `Shift+Tab` | Move focus between sidebar / items / detail |
| `Enter` | Drill into the highlighted item; on a Skills or Agents row inside a plugin, opens a detail modal |
| `r` | Re-scan disk |
| `o` | Open the highlighted item's file in `$EDITOR` (TUI suspends + resumes) |
| `y` | Yank the highlighted item's path to the clipboard (OSC 52) |
| `b` | Yank the rendered body — works on memory entries, slash commands, skills, agents, hooks |
| `/` | Show + focus the filter input (live-filters the items list) |
| `Esc` | Clear filter and refocus items |
| `?` | Help overlay listing every binding |
| `q` | Quit |

The filter shows `(filtered: N / M)` in the title while active. Press Enter inside the filter to keep the filter and jump back to the items list; press Esc to clear.

## Theming

Built on Textual — the theme follows `Ctrl+P` (palette) and reflows colours across cards, borders, badges, and severity indicators. Default theme is `textual-dark`.

## Supported agent CLIs

- **Claude Code** — full support today.
- **Codex / Gemini CLI / Cursor** — planned. The internal `Source` protocol is in place so adding another CLI is a new `Source` implementation, not a rewrite.

## License

GPL-3.0-only. See `LICENSE`.
