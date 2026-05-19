import functools
import json
import re
import shlex
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, cast

from agentpeek.models import (
    HookSpec,
    KeybindingEntry,
    KeybindingsBundle,
    MCPServer,
    MemoryFile,
    MemoryKind,
    Plugin,
    PluginInstallation,
    ScanResult,
    ScanWarning,
    SettingsBundle,
    SlashCommand,
)
from agentpeek.parsers import load_frontmatter, load_json
from agentpeek.parsers.coerce import (
    as_dict,
    as_int,
    as_str,
    as_str_dict,
    as_str_tuple,
)
from agentpeek.parsers.plugin_contents import parse_plugin_contents


class LocalSource:
    name: str = "local"
    DEFAULT_DIR_NAME: ClassVar[str] = ".claude"

    def default_root(self) -> Path:
        return Path.home() / self.DEFAULT_DIR_NAME

    def detect(self, root: Path) -> bool:
        # A directory looks like a Claude Code config root if it contains any
        # of these known artifacts. settings.local.json alone is enough —
        # Claude Code commonly auto-creates that in projects without ever
        # writing a settings.json.
        indicators = (
            "settings.json",
            "settings.local.json",
            "remote-settings.json",
            "commands",
            "plugins",
            "hooks",
            "keybindings.json",
            "CLAUDE.md",
        )
        return any((root / name).exists() for name in indicators)

    def scan(self, root: Path) -> ScanResult:
        warnings: list[ScanWarning] = []
        settings = self._scan_settings(root, warnings)
        hooks = self._scan_hooks(settings, root, warnings)
        commands = self._scan_commands(root, warnings)
        plugins = self._scan_plugins(root, warnings)
        memory = self._scan_memory(root, warnings)
        keybindings = self._scan_keybindings(root, warnings)
        mcp = self._scan_mcp(root, warnings)
        return ScanResult(
            source=self.name,
            root=root,
            settings=settings,
            hooks=hooks,
            commands=commands,
            plugins=plugins,
            memory=memory,
            keybindings=keybindings,
            mcp=mcp,
            warnings=tuple(warnings),
        )

    def _scan_settings(
        self, root: Path, warnings: list[ScanWarning]
    ) -> SettingsBundle | None:
        user_path = root / "settings.json"
        local_path = root / "settings.local.json"

        user_data = _safe_load_json_dict(user_path, "settings", warnings)
        local_data = _safe_load_json_dict(local_path, "settings", warnings)

        if not user_path.exists() and not local_path.exists():
            return None

        env_user = as_str_dict(user_data.get("env"))
        env_local = as_str_dict(local_data.get("env"))
        env = {**env_user, **env_local}

        permissions = as_dict(local_data.get("permissions")) or as_dict(
            user_data.get("permissions")
        )
        permissions_allow = as_str_tuple(
            permissions.get("allow") if permissions else None
        )
        permissions_deny = as_str_tuple(
            permissions.get("deny") if permissions else None
        )
        permissions_ask = as_str_tuple(permissions.get("ask") if permissions else None)

        user_plugins = as_dict(user_data.get("enabledPlugins")) or {}
        local_plugins = as_dict(local_data.get("enabledPlugins")) or {}
        enabled_set: set[str] = set()
        for k, v in {**user_plugins, **local_plugins}.items():
            if bool(v):
                enabled_set.add(str(k))
        enabled_plugins = tuple(sorted(enabled_set))

        hooks_raw_dict = as_dict(user_data.get("hooks")) or {}
        hooks_raw: dict[str, tuple[dict[str, object], ...]] = {}
        for event, entries in hooks_raw_dict.items():
            if isinstance(entries, list):
                event_entries: list[dict[str, object]] = []
                for e in cast("list[object]", entries):
                    if isinstance(e, dict):
                        event_entries.append(cast("dict[str, object]", e))
                hooks_raw[str(event)] = tuple(event_entries)

        hooks_dir = root / "hooks"
        hooks_dir_files = (
            sum(1 for _ in hooks_dir.iterdir()) if hooks_dir.is_dir() else 0
        )

        return SettingsBundle(
            user_settings_path=user_path if user_path.exists() else None,
            local_settings_path=local_path if local_path.exists() else None,
            model=as_str(user_data.get("model")),
            theme=as_str(user_data.get("theme")),
            editor_mode=as_str(user_data.get("editorMode")),
            effort_level=as_str(user_data.get("effortLevel")),
            output_style=as_str(user_data.get("outputStyle")),
            env=MappingProxyType(env),
            permissions_allow=permissions_allow,
            permissions_deny=permissions_deny,
            permissions_ask=permissions_ask,
            enabled_plugins=enabled_plugins,
            hooks_raw=MappingProxyType(hooks_raw),
            hooks_dir_files=hooks_dir_files,
        )

    def _scan_hooks(
        self,
        settings: SettingsBundle | None,
        root: Path,
        warnings: list[ScanWarning],
    ) -> tuple[HookSpec, ...]:
        if settings is None:
            return ()
        hooks: list[HookSpec] = []
        for event, entries in settings.hooks_raw.items():
            for entry in entries:
                matcher = as_str(entry.get("matcher"))
                inner_list = entry.get("hooks")
                if not isinstance(inner_list, list):
                    continue
                for inner in cast("list[object]", inner_list):
                    if not isinstance(inner, dict):
                        continue
                    inner_d = cast("dict[str, object]", inner)
                    cmd = as_str(inner_d.get("command"))
                    if cmd is None:
                        continue
                    resolved = _resolve_script(cmd, root)
                    if resolved is None:
                        warnings.append(
                            ScanWarning(
                                path=None,
                                category="hooks",
                                reason=(
                                    f"could not parse hook command "
                                    f"(unmatched quote?): {cmd[:80]}"
                                ),
                            )
                        )
                        referenced, dynamic = None, False
                    else:
                        referenced, dynamic = resolved
                    exists = referenced is not None and referenced.exists()
                    if referenced is not None and not exists and not dynamic:
                        warnings.append(
                            ScanWarning(
                                path=referenced,
                                category="hooks",
                                reason=f"referenced script not found: {referenced}",
                            )
                        )
                    hooks.append(
                        HookSpec(
                            event=event,
                            matcher=matcher,
                            type=as_str(inner_d.get("type")) or "command",
                            command=cmd,
                            timeout=as_int(inner_d.get("timeout")),
                            referenced_script=referenced,
                            script_exists=exists,
                            referenced_dynamic=dynamic,
                        )
                    )
        return tuple(hooks)

    def _scan_commands(
        self, root: Path, warnings: list[ScanWarning]
    ) -> tuple[SlashCommand, ...]:
        commands_dir = root / "commands"
        if not commands_dir.is_dir():
            return ()
        results: list[SlashCommand] = []
        for md_path in sorted(commands_dir.rglob("*.md")):
            file, warning = load_frontmatter(md_path, category="commands")
            if warning is not None:
                warnings.append(warning)
            name = str(md_path.relative_to(commands_dir).with_suffix("")).replace(
                "\\", "/"
            )
            if file is None:
                # Frontmatter parse failed — fall back to raw text so the
                # detail pane can still show what's in the file. The warning
                # is already attached above.
                try:
                    raw_body = md_path.read_text(encoding="utf-8")
                except OSError:
                    raw_body = ""
                results.append(
                    SlashCommand(
                        path=md_path,
                        name=name,
                        description=None,
                        argument_hint=None,
                        allowed_tools=(),
                        body=raw_body,
                    )
                )
                continue
            allowed = as_str(file.metadata.get("allowed-tools"))
            allowed_tuple: tuple[str, ...] = (
                tuple(s.strip() for s in allowed.split(",") if s.strip())
                if allowed
                else ()
            )
            results.append(
                SlashCommand(
                    path=md_path,
                    name=name,
                    description=as_str(file.metadata.get("description")),
                    argument_hint=as_str(file.metadata.get("argument-hint")),
                    allowed_tools=allowed_tuple,
                    body=file.body,
                )
            )
        return tuple(results)

    def _scan_plugins(
        self, root: Path, warnings: list[ScanWarning]
    ) -> tuple[Plugin, ...]:
        registry_path = root / "plugins" / "installed_plugins.json"
        if not registry_path.exists():
            return ()
        data, warning = load_json(registry_path, category="plugins")
        if warning is not None:
            warnings.append(warning)
        if not isinstance(data, dict):
            return ()
        plugins_obj = cast("dict[str, object]", data).get("plugins")
        if not isinstance(plugins_obj, dict):
            return ()

        enabled_union = _collect_enabled_plugins(root, warnings)

        results: list[Plugin] = []
        for qid, installs_obj in cast("dict[str, object]", plugins_obj).items():
            if not isinstance(installs_obj, list):
                continue
            qid_str = str(qid)
            pid, marketplace = (
                qid_str.rsplit("@", 1) if "@" in qid_str else (qid_str, "")
            )
            installations = _parse_installations(cast("list[object]", installs_obj))
            # Enumerate contents from the first installation's directory.
            # Plugins typically pin to one version per qualified id, so
            # there's only one set of contents to surface.
            contents = (
                parse_plugin_contents(
                    installations[0].install_path, qualified_id=qid_str
                )
                if installations and installations[0].install_path.is_dir()
                else None
            )
            if contents is not None:
                warnings.extend(contents.warnings)
            results.append(
                Plugin(
                    id=pid,
                    marketplace=marketplace,
                    qualified_id=qid_str,
                    enabled=qid_str in enabled_union,
                    installations=installations,
                    manifest=contents.manifest if contents else None,
                    skills=contents.skills if contents else (),
                    agents=contents.agents if contents else (),
                    commands=contents.commands if contents else (),
                    hooks=contents.hooks if contents else (),
                    mcps=contents.mcps if contents else (),
                )
            )
        return tuple(results)

    def _scan_memory(
        self, root: Path, warnings: list[ScanWarning]
    ) -> tuple[MemoryFile, ...]:
        results: list[MemoryFile] = []
        candidate = root / "CLAUDE.md"
        if candidate.is_file():
            results.append(
                _read_memory_file(
                    candidate,
                    kind="claude_md",
                    project_label=None,
                    warnings=warnings,
                )
            )
        projects_dir = root / "projects"
        if projects_dir.is_dir():
            for proj_dir in sorted(projects_dir.iterdir()):
                mem_dir = proj_dir / "memory"
                if not mem_dir.is_dir():
                    continue
                label = _resolve_project_label(proj_dir)
                for md_path in sorted(mem_dir.glob("*.md")):
                    kind: MemoryKind = (
                        "memory_index"
                        if md_path.name == "MEMORY.md"
                        else "memory_entry"
                    )
                    results.append(
                        _read_memory_file(
                            md_path,
                            kind=kind,
                            project_label=label,
                            warnings=warnings,
                        )
                    )
        return tuple(results)

    def _scan_keybindings(
        self, root: Path, warnings: list[ScanWarning]
    ) -> KeybindingsBundle | None:
        path = root / "keybindings.json"
        if not path.exists():
            return None
        data, warning = load_json(path, category="keybindings")
        if warning is not None:
            warnings.append(warning)
        entries: list[KeybindingEntry] = []
        if isinstance(data, dict):
            data_d = cast("dict[str, object]", data)
            bindings_outer = data_d.get("bindings")
            if isinstance(bindings_outer, list):
                for ctx_obj in cast("list[object]", bindings_outer):
                    if not isinstance(ctx_obj, dict):
                        continue
                    ctx_d = cast("dict[str, object]", ctx_obj)
                    context = as_str(ctx_d.get("context")) or ""
                    inner = ctx_d.get("bindings")
                    if isinstance(inner, dict):
                        for key, action in cast("dict[str, object]", inner).items():
                            entries.append(
                                KeybindingEntry(
                                    context=context,
                                    key=str(key),
                                    action=str(action),
                                )
                            )
        return KeybindingsBundle(path=path, entries=tuple(entries))

    def _scan_mcp(
        self, root: Path, warnings: list[ScanWarning]
    ) -> tuple[MCPServer, ...]:
        results: list[MCPServer] = []
        for filename in ("settings.json", "remote-settings.json"):
            path = root / filename
            if not path.exists():
                continue
            data, warning = load_json(path, category="mcp")
            if warning is not None:
                warnings.append(warning)
            if not isinstance(data, dict):
                continue
            data_d = cast("dict[str, object]", data)
            servers = data_d.get("mcpServers")
            if not isinstance(servers, dict):
                continue
            for srv_name, srv in cast("dict[str, object]", servers).items():
                if not isinstance(srv, dict):
                    continue
                srv_d = cast("dict[str, object]", srv)
                args_raw = srv_d.get("args")
                args_tuple: tuple[str, ...] = (
                    tuple(str(a) for a in cast("list[object]", args_raw))
                    if isinstance(args_raw, list)
                    else ()
                )
                env_obj = as_str_dict(srv_d.get("env"))
                results.append(
                    MCPServer(
                        name=str(srv_name),
                        source_path=path,
                        command=as_str(srv_d.get("command")),
                        args=args_tuple,
                        env=MappingProxyType(env_obj),
                    )
                )
        return tuple(results)


_DEFAULT_VAR_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}")
_BARE_VAR_RE = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?")


def _resolve_script(
    command: str, root: Path
) -> tuple[Path | None, bool] | None:
    """Best-effort extraction of the script path from a hook command.

    Returns `(path, is_dynamic)`, or `None` if the command can't be
    shell-tokenized (unmatched quote, etc.) — callers should emit a
    warning and skip script resolution rather than substring-matching
    against malformed tokens.

    Handles `~/.claude`, `$HOME`, `${VAR:-default}`, `${CLAUDE_PROJECT_DIR}`-
    style env vars, absolute paths under `root`, and quoted tokens. When
    a token contains `.claude/<tail>`, the tail is anchored against `root`
    — true for the common pattern where hook commands reference files
    inside the same `.claude/` they live in.

    `is_dynamic` flags that at least one unresolved env-var reference
    was seen; diagnostic only, doesn't affect orphan-hook logic since
    the `.claude/<tail>` anchoring already resolves the typical case.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None

    is_dynamic = False
    root_str = str(root)
    home_str = str(Path.home())

    for tok in tokens:
        if _DEFAULT_VAR_RE.search(tok):
            is_dynamic = True
        normalized = _DEFAULT_VAR_RE.sub(lambda m: m.group(1), tok)
        normalized = normalized.replace("${HOME}", home_str).replace(
            "$HOME", home_str
        )
        if _BARE_VAR_RE.search(normalized):
            is_dynamic = True
            normalized = _BARE_VAR_RE.sub("", normalized)
        if normalized.startswith("~/"):
            normalized = home_str + normalized[1:]
        if normalized.startswith(root_str):
            return Path(normalized), is_dynamic
        idx = normalized.find(".claude/")
        if idx >= 0:
            tail = normalized[idx + len(".claude/") :]
            if tail:
                return root / tail, is_dynamic
        if root_str in normalized:
            j = normalized.find(root_str)
            return Path(normalized[j:]), is_dynamic

    return None, is_dynamic


def _safe_load_json_dict(
    path: Path, category: str, warnings: list[ScanWarning]
) -> dict[str, object]:
    if not path.exists():
        return {}
    data, warning = load_json(path, category=category)
    if warning is not None:
        warnings.append(warning)
    if isinstance(data, dict):
        return cast("dict[str, object]", data)
    return {}


def _collect_enabled_plugins(root: Path, warnings: list[ScanWarning]) -> set[str]:
    user_data = _safe_load_json_dict(root / "settings.json", "plugins", warnings)
    remote_data = _safe_load_json_dict(
        root / "remote-settings.json", "plugins", warnings
    )
    enabled_user = as_dict(user_data.get("enabledPlugins")) or {}
    enabled_remote = as_dict(remote_data.get("enabledPlugins")) or {}

    for qid in set(enabled_user) & set(enabled_remote):
        if bool(enabled_user[qid]) != bool(enabled_remote[qid]):
            warnings.append(
                ScanWarning(
                    path=None,
                    category="plugins",
                    reason=(
                        f"enabledPlugins conflict for {qid}: "
                        f"settings.json={bool(enabled_user[qid])}, "
                        f"remote-settings.json={bool(enabled_remote[qid])}"
                    ),
                )
            )

    return {str(k) for k, v in {**enabled_user, **enabled_remote}.items() if bool(v)}


def _read_memory_file(
    path: Path,
    *,
    kind: MemoryKind,
    project_label: str | None,
    warnings: list[ScanWarning],
) -> MemoryFile:
    file, warning = load_frontmatter(path, category="memory")
    if warning is not None:
        warnings.append(warning)
    if file is None:
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            body = ""
        has_fm = False
    else:
        body = file.body
        has_fm = bool(file.metadata)
    return MemoryFile(
        path=path,
        body=body,
        has_frontmatter=has_fm,
        kind=kind,
        project_label=project_label,
    )


# Cap how many session logs we crack open per project. A handful is
# enough — Claude Code writes cwd on every session — and we don't want
# to walk a long archive on a project with hundreds of sessions.
_MAX_JSONL_PROBES = 8


@functools.lru_cache(maxsize=512)
def _resolve_project_label(proj_dir: Path) -> str:
    """Return the original cwd for a Claude Code project directory.

    Claude Code's encoding of project paths into the directory name
    collapses both `/` and `.` to `-` and is unrecoverable for paths
    containing a literal `-`. The authoritative source is the `cwd`
    field embedded in any `.jsonl` session log under the project dir.
    Falls back to the raw directory name when no jsonl yields a cwd.
    """
    for jsonl in sorted(proj_dir.glob("*.jsonl"))[:_MAX_JSONL_PROBES]:
        try:
            with jsonl.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(rec, dict):
                        cwd = cast("dict[str, object]", rec).get("cwd")
                        if isinstance(cwd, str) and cwd:
                            return cwd
        except OSError:
            continue
    return proj_dir.name


def _parse_installations(
    installs_obj: list[object],
) -> tuple[PluginInstallation, ...]:
    installations: list[PluginInstallation] = []
    for inst in installs_obj:
        if not isinstance(inst, dict):
            continue
        inst_d = cast("dict[str, object]", inst)
        install_path_str = as_str(inst_d.get("installPath")) or ""
        project_path_str = as_str(inst_d.get("projectPath"))
        installations.append(
            PluginInstallation(
                scope=as_str(inst_d.get("scope")) or "",
                install_path=Path(install_path_str),
                version=as_str(inst_d.get("version")) or "",
                installed_at=as_str(inst_d.get("installedAt")) or "",
                last_updated=as_str(inst_d.get("lastUpdated")) or "",
                git_commit_sha=as_str(inst_d.get("gitCommitSha")),
                project_path=Path(project_path_str) if project_path_str else None,
            )
        )
    return tuple(installations)
