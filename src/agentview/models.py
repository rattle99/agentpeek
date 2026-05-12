from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
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


@dataclass(frozen=True, slots=True)
class SlashCommand:
    path: Path
    name: str
    description: str | None
    argument_hint: str | None
    allowed_tools: tuple[str, ...]
    body: str


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
class Plugin:
    id: str
    marketplace: str
    qualified_id: str
    enabled: bool
    installations: tuple[PluginInstallation, ...]


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
    cross_scope_warnings: tuple[ScanWarning, ...] = ()

    @property
    def primary(self) -> ScanResult | None:
        return self.project or self.user
