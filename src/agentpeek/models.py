from collections.abc import Mapping
from dataclasses import dataclass
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
    # {restriction_name: allowed_bool}. Empty when the file is absent.
    policy_restrictions: Mapping[str, bool]
    # Enterprise-pushed remote-settings.json extras. Surfaced so users
    # can see what their managed config injected into the session.
    company_announcements: tuple[str, ...]
    spinner_tips: tuple[str, ...]
    # Relative paths inside `<root>/local/` — typically user-applied
    # patches or scratch scripts that aren't Claude Code config but
    # may shadow or modify the canonical hooks. Surfaced so the user
    # can see what's there.
    local_overrides: tuple[str, ...]
    env: Mapping[str, str]
    permissions_allow: tuple[str, ...]
    permissions_deny: tuple[str, ...]
    permissions_ask: tuple[str, ...]
    enabled_plugins: tuple[str, ...]
    hooks_raw: Mapping[str, tuple[Mapping[str, object], ...]]
    hooks_dir_files: int


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


@dataclass(frozen=True, slots=True)
class PluginSkill:
    path: Path
    name: str
    description: str | None
    body: str
    source_plugin: str | None = None


@dataclass(frozen=True, slots=True)
class PluginAgent:
    path: Path
    name: str
    description: str | None
    body: str
    source_plugin: str | None = None


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
    marketplace_source: Mapping[str, str] = MappingProxyType({})


MemoryKind = Literal["claude_md", "memory_index", "memory_entry"]


@dataclass(frozen=True, slots=True)
class MemoryFile:
    path: Path
    body: str
    has_frontmatter: bool
    kind: MemoryKind
    project_label: str | None


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
