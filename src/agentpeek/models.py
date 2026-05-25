from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal


@dataclass(frozen=True, slots=True)
class ScanWarning:
    path: Path | None
    category: str
    reason: str


@dataclass(frozen=True, slots=True)
class SettingsBundle:
    user_settings_path: Path | None
    local_settings_path: Path | None
    model: str | None
    theme: str | None
    editor_mode: str | None
    effort_level: str | None
    output_style: str | None
    # `statusLine` is either {"type": "command", "command": "..."} or a
    # legacy string; normalized to dict shape (or None when absent).
    status_line: Mapping[str, str] | None
    skip_auto_permission_prompt: bool
    # `policy-limits.json` from this scope, flattened to
    # {restriction_name: "allowed" / "denied" / "denied — <message>"}.
    # Empty when the file is absent.
    policy_restrictions: Mapping[str, str]
    # Enterprise-pushed remote-settings.json extras. Surfaced so users
    # can see what their managed config injected into the session.
    company_announcements: tuple[str, ...]
    spinner_tips: tuple[str, ...]
    env: Mapping[str, str]
    permissions_allow: tuple[str, ...]
    permissions_deny: tuple[str, ...]
    permissions_ask: tuple[str, ...]
    enabled_plugins: tuple[str, ...]
    hooks_raw: Mapping[str, tuple[Mapping[str, object], ...]]
    # Filenames (relative to `<root>/hooks/`) present on disk —
    # surfaced as a list so users can see actual script names without
    # opening the filesystem.
    hooks_dir_files: tuple[str, ...]
    # `agent` setting: default subagent for sessions in this scope.
    # Set via `.claude/settings.json` and overridable on the CLI.
    default_agent: str | None = None
    # `claudeMdExcludes`: glob patterns excluding specific CLAUDE.md
    # files from the load order. Surfaced because they answer
    # "why isn't this CLAUDE.md taking effect?" debugging questions.
    claude_md_excludes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HookSpec:
    event: str
    matcher: str | None
    type: str
    command: str
    timeout: int | None
    referenced_script: Path | None
    script_exists: bool
    # True when the command references a runtime env var (e.g.
    # ${CLAUDE_PROJECT_DIR}) — diagnostic flag, the resolver still tries
    # to anchor any `.claude/<tail>` reference against the scan root.
    referenced_dynamic: bool = False
    # `source_plugin` is the qualified id of a plugin (`name@market`) when
    # this hook was contributed by an installed plugin. None for hooks
    # declared in a user or project settings file.
    source_plugin: str | None = None


@dataclass(frozen=True, slots=True)
class SlashCommand:
    path: Path
    name: str
    description: str | None
    argument_hint: str | None
    allowed_tools: tuple[str, ...]
    body: str
    source_plugin: str | None = None


@dataclass(frozen=True, slots=True)
class PluginInstallation:
    scope: str
    install_path: Path
    version: str
    installed_at: str
    last_updated: str
    git_commit_sha: str | None
    project_path: Path | None


@dataclass(frozen=True, slots=True)
class PluginManifest:
    description: str | None
    version: str | None
    author_name: str | None
    author_email: str | None
    homepage: str | None
    license: str | None
    keywords: tuple[str, ...] = ()
    # Declared name (`name` in plugin.json) — may differ from the
    # install directory name. Surfaced so a renamed/forked plugin
    # is recognizable.
    name: str | None = None
    # Upstream source repo URL. Accepts both bare-string form
    # ("https://github.com/...") and the dict form ({"type": "git",
    # "url": "..."}); normalized to a URL string here.
    repository: str | None = None
    # `requires` from plugin.json — qualified ids of plugins this
    # plugin depends on. Empty when not declared.
    requires: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginSkill:
    path: Path
    name: str
    description: str | None
    body: str
    source_plugin: str | None = None
    # `when_to_use` — trigger guidance shown to Claude Code so it
    # knows when to auto-load this skill. Often more descriptive
    # than `description`; surfaced here so users can see the
    # activation contract.
    when_to_use: str | None = None
    # True/False if `user-invocable` is set in frontmatter; None
    # when the field is absent (defaults vary).
    user_invocable: bool | None = None
    # Comma-separated tool allow/deny lists from frontmatter.
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginAgent:
    path: Path
    name: str
    description: str | None
    body: str
    source_plugin: str | None = None
    when_to_use: str | None = None
    user_invocable: bool | None = None
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Plugin:
    id: str
    marketplace: str
    qualified_id: str
    enabled: bool
    installations: tuple[PluginInstallation, ...]
    # Contents enumerated from the first installation's `install_path`.
    # All-optional / empty-default so existing test constructors and
    # future non-Claude sources (e.g. a CodexSource) can build Plugin
    # without populating these.
    manifest: PluginManifest | None = None
    skills: tuple[PluginSkill, ...] = ()
    agents: tuple[PluginAgent, ...] = ()
    commands: tuple[SlashCommand, ...] = ()
    hooks: tuple[HookSpec, ...] = ()
    mcps: tuple["MCPServer", ...] = ()
    # `~/.claude/plugins/blocklist.json` entries override any
    # `enabled=True` claim — Claude Code refuses to load a blocklisted
    # plugin regardless of settings.
    blocked: bool = False
    blocked_reason: str | None = None
    # Where this plugin's marketplace lives — resolved from
    # `~/.claude/plugins/known_marketplaces.json` and the
    # `extraKnownMarketplaces` keys of user + remote settings. Empty
    # mapping when the marketplace isn't found in any registry.
    # `default_factory` (not bare default) because Python 3.11's
    # @dataclass rejects MappingProxyType as a mutable default.
    marketplace_source: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )


MemoryKind = Literal[
    "claude_md",
    "claude_local_md",
    "memory_index",
    "memory_entry",
    "agent_memory_index",
    "agent_memory_entry",
]


@dataclass(frozen=True, slots=True)
class MemoryImport:
    # Resolved import target encountered while expanding `@path/to/file`
    # references inside a CLAUDE.md body. `resolved_path` is None when
    # the import couldn't be resolved (file missing, cycle, depth cap
    # hit); `reason` carries a short marker for the renderer.
    raw: str
    resolved_path: Path | None
    depth: int
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryFile:
    path: Path
    body: str
    has_frontmatter: bool
    kind: MemoryKind
    project_label: str | None
    # Auto-memory entries belonging to a specific subagent carry the
    # agent name resolved from the parent directory; otherwise None.
    agent_name: str | None = None
    # Resolved @path imports referenced from this body (recursive, max 5
    # hops, cycles marked). Empty when the file isn't a CLAUDE.md or
    # contains no imports.
    imports: tuple[MemoryImport, ...] = ()


@dataclass(frozen=True, slots=True)
class Agent:
    # User- or project-scope subagent (`~/.claude/agents/` or
    # `<project>/.claude/agents/`). Plugin-bundled agents use the
    # separate PluginAgent type — they live inside Plugin.agents and
    # are constructed by parsers.plugin_contents.
    path: Path
    name: str
    description: str | None
    body: str
    # `tools` and `disallowedTools` accept either a comma-separated
    # string or a YAML list in the canonical schema. Normalized to a
    # tuple of token strings here.
    tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    model: str | None = None
    permission_mode: str | None = None
    max_turns: int | None = None
    skills: tuple[str, ...] = ()
    memory: str | None = None
    background: bool | None = None
    effort: str | None = None
    isolation: str | None = None
    color: str | None = None
    initial_prompt: str | None = None
    # Raw mcpServers + hooks bodies — both have complex schemas that
    # don't compress into a tuple of strings cleanly. We carry them as
    # presence flags + a stringified summary so the detail card can
    # show "configured" / "(none)".
    has_mcp_servers: bool = False
    has_hooks: bool = False


@dataclass(frozen=True, slots=True)
class Rule:
    # `.claude/rules/*.md` — path-scoped or always-loaded context rule.
    # Per docs, `paths:` frontmatter is optional; absent means the rule
    # loads at session start with the same priority as `.claude/CLAUDE.md`.
    path: Path
    name: str
    description: str | None
    paths_globs: tuple[str, ...]
    always_loaded: bool
    body: str


@dataclass(frozen=True, slots=True)
class KeybindingEntry:
    context: str
    key: str
    action: str


@dataclass(frozen=True, slots=True)
class KeybindingsBundle:
    path: Path | None
    entries: tuple[KeybindingEntry, ...]


@dataclass(frozen=True, slots=True)
class MCPServer:
    name: str
    source_path: Path
    command: str | None
    args: tuple[str, ...]
    env: Mapping[str, str]
    source_plugin: str | None = None
    # True when this MCP server appears in
    # `~/.claude/mcp-needs-auth-cache.json` — Claude Code remembers it
    # but the OAuth/auth flow hasn't completed, so it won't actually
    # connect until the user finishes auth.
    auth_pending: bool = False


@dataclass(frozen=True, slots=True)
class TrustEntry:
    # Per-project trust state from `~/.claude.json[projects][<path>]`.
    # Surfaced in the Settings detail card so users can see which
    # projects they've trusted, which tools are pre-approved, and the
    # per-project enable/disable lists for `.mcp.json` servers.
    project_path: str
    trust_accepted: bool
    allowed_tools: tuple[str, ...]
    enabled_mcpjson_servers: tuple[str, ...]
    disabled_mcpjson_servers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScanResult:
    source: str
    root: Path
    settings: SettingsBundle | None
    hooks: tuple[HookSpec, ...]
    commands: tuple[SlashCommand, ...]
    plugins: tuple[Plugin, ...]
    memory: tuple[MemoryFile, ...]
    keybindings: KeybindingsBundle | None
    mcp: tuple[MCPServer, ...]
    warnings: tuple[ScanWarning, ...]
    agents: tuple[Agent, ...] = ()
    rules: tuple[Rule, ...] = ()
    # `~/.claude.json` highlights, only populated at user scope. Empty
    # tuple when the file is absent or the scope isn't user.
    trust_entries: tuple[TrustEntry, ...] = ()
    oauth_session_present: bool = False
    claude_json_path: Path | None = None

    @classmethod
    def empty(
        cls, *, root: Path | None = None, reason: str | None = None
    ) -> "ScanResult":
        ws: tuple[ScanWarning, ...] = (
            (ScanWarning(path=None, category="source", reason=reason),)
            if reason
            else ()
        )
        return cls(
            source="",
            root=root or Path("/"),
            settings=None,
            hooks=(),
            commands=(),
            plugins=(),
            memory=(),
            keybindings=None,
            mcp=(),
            warnings=ws,
        )


@dataclass(frozen=True, slots=True)
class ScanReport:
    user: ScanResult | None
    project: ScanResult | None
    project_root: Path | None

    @property
    def primary(self) -> ScanResult | None:
        return self.project or self.user
