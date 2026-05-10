from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, cast

from agentview.models import (
    HookSpec,
    KeybindingEntry,
    KeybindingsBundle,
    MCPServer,
    MemoryFile,
    Plugin,
    PluginInstallation,
    ScanResult,
    ScanWarning,
    SettingsBundle,
    SlashCommand,
)
from agentview.parsers import load_frontmatter, load_json


class LocalSource:
    name: str = "local"
    DEFAULT_DIR_NAME: ClassVar[str] = ".claude"

    def default_root(self) -> Path:
        return Path.home() / self.DEFAULT_DIR_NAME

    def detect(self, root: Path) -> bool:
        return (root / "settings.json").exists() or (root / "commands").is_dir()

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

        env_user = _as_str_dict(user_data.get("env"))
        env_local = _as_str_dict(local_data.get("env"))
        env = {**env_user, **env_local}

        permissions = _as_dict(local_data.get("permissions")) or _as_dict(
            user_data.get("permissions")
        )
        permissions_allow = _as_str_tuple(
            permissions.get("allow") if permissions else None
        )
        permissions_deny = _as_str_tuple(
            permissions.get("deny") if permissions else None
        )
        permissions_ask = _as_str_tuple(permissions.get("ask") if permissions else None)

        user_plugins = _as_dict(user_data.get("enabledPlugins")) or {}
        local_plugins = _as_dict(local_data.get("enabledPlugins")) or {}
        enabled_set: set[str] = set()
        for k, v in {**user_plugins, **local_plugins}.items():
            if bool(v):
                enabled_set.add(str(k))
        enabled_plugins = tuple(sorted(enabled_set))

        hooks_raw_dict = _as_dict(user_data.get("hooks")) or {}
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
            model=_as_str(user_data.get("model")),
            theme=_as_str(user_data.get("theme")),
            editor_mode=_as_str(user_data.get("editorMode")),
            effort_level=_as_str(user_data.get("effortLevel")),
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
                matcher = _as_str(entry.get("matcher"))
                inner_list = entry.get("hooks")
                if not isinstance(inner_list, list):
                    continue
                for inner in cast("list[object]", inner_list):
                    if not isinstance(inner, dict):
                        continue
                    inner_d = cast("dict[str, object]", inner)
                    cmd = _as_str(inner_d.get("command"))
                    if cmd is None:
                        continue
                    referenced = _resolve_script(cmd, root)
                    exists = referenced is not None and referenced.exists()
                    if referenced is not None and not exists:
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
                            type=_as_str(inner_d.get("type")) or "command",
                            command=cmd,
                            timeout=_as_int(inner_d.get("timeout")),
                            referenced_script=referenced,
                            script_exists=exists,
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
            allowed = _as_str(file.metadata.get("allowed-tools"))
            allowed_tuple: tuple[str, ...] = (
                tuple(s.strip() for s in allowed.split(",") if s.strip())
                if allowed
                else ()
            )
            results.append(
                SlashCommand(
                    path=md_path,
                    name=name,
                    description=_as_str(file.metadata.get("description")),
                    argument_hint=_as_str(file.metadata.get("argument-hint")),
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
            results.append(
                Plugin(
                    id=pid,
                    marketplace=marketplace,
                    qualified_id=qid_str,
                    enabled=qid_str in enabled_union,
                    installations=_parse_installations(
                        cast("list[object]", installs_obj)
                    ),
                )
            )
        return tuple(results)

    def _scan_memory(
        self, root: Path, warnings: list[ScanWarning]
    ) -> tuple[MemoryFile, ...]:
        results: list[MemoryFile] = []
        candidate = root / "CLAUDE.md"
        if candidate.is_file():
            file, warning = load_frontmatter(candidate, category="memory")
            if warning is not None:
                warnings.append(warning)
            if file is None:
                try:
                    body = candidate.read_text(encoding="utf-8")
                except OSError:
                    body = ""
                has_fm = False
            else:
                body = file.body
                has_fm = bool(file.metadata)
            results.append(
                MemoryFile(path=candidate, body=body, has_frontmatter=has_fm)
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
                    context = _as_str(ctx_d.get("context")) or ""
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
                env_obj = _as_str_dict(srv_d.get("env"))
                results.append(
                    MCPServer(
                        name=str(srv_name),
                        source_path=path,
                        command=_as_str(srv_d.get("command")),
                        args=args_tuple,
                        env=MappingProxyType(env_obj),
                    )
                )
        return tuple(results)


def _as_str(v: object) -> str | None:
    return v if isinstance(v, str) else None


def _as_int(v: object) -> int | None:
    if isinstance(v, bool):
        return None
    return v if isinstance(v, int) else None


def _as_dict(v: object) -> dict[str, object] | None:
    if isinstance(v, dict):
        return cast("dict[str, object]", v)
    return None


def _as_str_dict(v: object) -> dict[str, str]:
    if isinstance(v, dict):
        return {
            str(k): str(val)
            for k, val in cast("dict[str, object]", v).items()
            if isinstance(val, str)
        }
    return {}


def _as_str_tuple(v: object) -> tuple[str, ...]:
    if isinstance(v, list):
        return tuple(s for s in cast("list[object]", v) if isinstance(s, str))
    return ()


def _resolve_script(command: str, root: Path) -> Path | None:
    for tok in command.split():
        if "~/.claude" in tok:
            return Path(tok.replace("~/.claude", str(root)))
        if str(root) in tok:
            return Path(tok)
    return None


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
    enabled_user = _as_dict(user_data.get("enabledPlugins")) or {}
    enabled_remote = _as_dict(remote_data.get("enabledPlugins")) or {}

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


def _parse_installations(
    installs_obj: list[object],
) -> tuple[PluginInstallation, ...]:
    installations: list[PluginInstallation] = []
    for inst in installs_obj:
        if not isinstance(inst, dict):
            continue
        inst_d = cast("dict[str, object]", inst)
        install_path_str = _as_str(inst_d.get("installPath")) or ""
        project_path_str = _as_str(inst_d.get("projectPath"))
        installations.append(
            PluginInstallation(
                scope=_as_str(inst_d.get("scope")) or "",
                install_path=Path(install_path_str),
                version=_as_str(inst_d.get("version")) or "",
                installed_at=_as_str(inst_d.get("installedAt")) or "",
                last_updated=_as_str(inst_d.get("lastUpdated")) or "",
                git_commit_sha=_as_str(inst_d.get("gitCommitSha")),
                project_path=Path(project_path_str) if project_path_str else None,
            )
        )
    return tuple(installations)
