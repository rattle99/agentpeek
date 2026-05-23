import functools
import json
import re
import shlex
from collections.abc import Mapping
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
from agentpeek.parsers.frontmatter_parser import (
    read_str_field,
    read_str_or_list_field,
    read_string_list_field,
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
        remote_path = root / "remote-settings.json"

        user_data = _safe_load_json_dict(user_path, "settings", warnings)
        local_data = _safe_load_json_dict(local_path, "settings", warnings)
        remote_data = _safe_load_json_dict(remote_path, "settings", warnings)

        if not user_path.exists() and not local_path.exists():
            return None

        env = {
            **as_str_dict(user_data.get("env")),
            **as_str_dict(local_data.get("env")),
        }

        # Per Claude Code docs, permission rules MERGE across scopes
        # rather than override. Union allow/deny/ask from both files.
        user_perms = as_dict(user_data.get("permissions")) or {}
        local_perms = as_dict(local_data.get("permissions")) or {}
        permissions_allow = _union_str_tuple(
            as_str_tuple(user_perms.get("allow")),
            as_str_tuple(local_perms.get("allow")),
        )
        permissions_deny = _union_str_tuple(
            as_str_tuple(user_perms.get("deny")),
            as_str_tuple(local_perms.get("deny")),
        )
        permissions_ask = _union_str_tuple(
            as_str_tuple(user_perms.get("ask")),
            as_str_tuple(local_perms.get("ask")),
        )
        _warn_permission_collisions(
            permissions_allow,
            permissions_deny,
            path=user_path if user_path.exists() else local_path,
            warnings=warnings,
        )

        enabled_plugins = _merge_enabled_plugins(user_data, local_data)
        hooks_raw = _merge_hooks_raw(user_data, local_data)
        hooks_dir_files = _list_hooks_dir_files(root)

        # statusLine can be a dict ({"type": "command", "command": "..."})
        # or, legacy, a bare string. Local overrides user.
        status_line = _parse_status_line(local_data.get("statusLine")) or (
            _parse_status_line(user_data.get("statusLine"))
        )
        skip_prompt = _resolve_skip_prompt(user_data, local_data)
        policy_restrictions = _load_policy_limits(root, warnings)

        company_announcements = as_str_tuple(remote_data.get("companyAnnouncements"))
        spinner_override = as_dict(remote_data.get("spinnerTipsOverride")) or {}
        spinner_tips = as_str_tuple(spinner_override.get("tips"))

        return SettingsBundle(
            user_settings_path=user_path if user_path.exists() else None,
            local_settings_path=local_path if local_path.exists() else None,
            model=as_str(user_data.get("model")),
            theme=as_str(user_data.get("theme")),
            editor_mode=as_str(user_data.get("editorMode")),
            effort_level=as_str(user_data.get("effortLevel")),
            output_style=as_str(user_data.get("outputStyle")),
            status_line=status_line,
            skip_auto_permission_prompt=skip_prompt,
            env=MappingProxyType(env),
            permissions_allow=permissions_allow,
            permissions_deny=permissions_deny,
            permissions_ask=permissions_ask,
            enabled_plugins=enabled_plugins,
            policy_restrictions=MappingProxyType(policy_restrictions),
            company_announcements=company_announcements,
            spinner_tips=spinner_tips,
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
                results.append(
                    SlashCommand(
                        path=md_path,
                        name=name,
                        description=None,
                        argument_hint=None,
                        allowed_tools=(),
                        body=_safe_read_text(md_path),
                    )
                )
                continue
            results.append(
                SlashCommand(
                    path=md_path,
                    name=name,
                    description=read_str_field(
                        file.metadata, "description",
                        path=md_path, category="commands", warnings=warnings,
                    ),
                    argument_hint=read_str_or_list_field(
                        file.metadata, "argument-hint",
                        path=md_path, category="commands", warnings=warnings,
                    ),
                    allowed_tools=read_string_list_field(
                        file.metadata, "allowed-tools",
                        path=md_path, category="commands", warnings=warnings,
                    ),
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
        blocklist = _load_blocklist(root, warnings)
        marketplaces = _load_marketplaces(root, warnings)

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
                    blocked=qid_str in blocklist,
                    blocked_reason=blocklist.get(qid_str),
                    marketplace_source=MappingProxyType(
                        marketplaces.get(marketplace, {})
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
        if not isinstance(data, dict):
            warnings.append(
                ScanWarning(
                    path=path,
                    category="keybindings",
                    reason="top-level value is not a JSON object",
                )
            )
            return KeybindingsBundle(path=path, entries=())
        data_d = cast("dict[str, object]", data)
        bindings_outer = data_d.get("bindings")
        if bindings_outer is None:
            return KeybindingsBundle(path=path, entries=())
        if not isinstance(bindings_outer, list):
            warnings.append(
                ScanWarning(
                    path=path,
                    category="keybindings",
                    reason=(
                        f"`bindings` is {type(bindings_outer).__name__}, "
                        "expected an array of contexts"
                    ),
                )
            )
            return KeybindingsBundle(path=path, entries=())
        for i, ctx_obj in enumerate(cast("list[object]", bindings_outer)):
            if not isinstance(ctx_obj, dict):
                warnings.append(
                    ScanWarning(
                        path=path,
                        category="keybindings",
                        reason=(
                            f"`bindings[{i}]` is "
                            f"{type(ctx_obj).__name__}, expected an object"
                        ),
                    )
                )
                continue
            ctx_d = cast("dict[str, object]", ctx_obj)
            context = as_str(ctx_d.get("context")) or ""
            inner = ctx_d.get("bindings")
            if inner is None:
                continue
            if not isinstance(inner, dict):
                warnings.append(
                    ScanWarning(
                        path=path,
                        category="keybindings",
                        reason=(
                            f"`bindings[{i}].bindings` is "
                            f"{type(inner).__name__}, expected an object"
                        ),
                    )
                )
                continue
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
        seen_names: set[str] = set()
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
                seen_names.add(str(srv_name))

        # Surface OAuth-based MCP servers Claude Code registered into
        # `mcp-needs-auth-cache.json`. These never appear in
        # `mcpServers` because they're configured through the
        # claude.ai UI, but they're real entries the user might want
        # to know about — and they won't actually work until auth
        # completes.
        auth_cache_path = root / "mcp-needs-auth-cache.json"
        if auth_cache_path.is_file():
            data, warning = load_json(auth_cache_path, category="mcp")
            if warning is not None:
                warnings.append(warning)
            if isinstance(data, dict):
                for name in cast("dict[str, object]", data):
                    if name in seen_names:
                        continue
                    results.append(
                        MCPServer(
                            name=str(name),
                            source_path=auth_cache_path,
                            command=None,
                            args=(),
                            env=MappingProxyType({}),
                            auth_pending=True,
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


def _union_str_tuple(*lists: tuple[str, ...]) -> tuple[str, ...]:
    """Order-preserving union of string sequences."""
    seen: list[str] = []
    for lst in lists:
        for item in lst:
            if item not in seen:
                seen.append(item)
    return tuple(seen)


def _safe_read_text(path: Path) -> str:
    """Read a text file, returning empty string on OSError."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _merge_enabled_plugins(
    user_data: dict[str, object], local_data: dict[str, object]
) -> tuple[str, ...]:
    """Union enabledPlugins across user + local settings, keeping truthy keys."""
    user_plugins = as_dict(user_data.get("enabledPlugins")) or {}
    local_plugins = as_dict(local_data.get("enabledPlugins")) or {}
    enabled: set[str] = set()
    for k, v in {**user_plugins, **local_plugins}.items():
        if bool(v):
            enabled.add(str(k))
    return tuple(sorted(enabled))


def _merge_hooks_raw(
    user_data: dict[str, object], local_data: dict[str, object]
) -> dict[str, tuple[dict[str, object], ...]]:
    """Union hook event entries across user + local. Entries from local
    follow entries from user within each event so ordering is preserved.
    """
    merged_events: dict[str, list[dict[str, object]]] = {}
    for source in (
        as_dict(user_data.get("hooks")) or {},
        as_dict(local_data.get("hooks")) or {},
    ):
        for event, entries in source.items():
            if not isinstance(entries, list):
                continue
            bucket = merged_events.setdefault(str(event), [])
            for entry in cast("list[object]", entries):
                if isinstance(entry, dict):
                    bucket.append(cast("dict[str, object]", entry))
    return {event: tuple(bucket) for event, bucket in merged_events.items()}


def _list_hooks_dir_files(root: Path) -> tuple[str, ...]:
    hooks_dir = root / "hooks"
    if not hooks_dir.is_dir():
        return ()
    return tuple(sorted(f.name for f in hooks_dir.iterdir() if f.is_file()))


def _resolve_skip_prompt(
    user_data: dict[str, object], local_data: dict[str, object]
) -> bool:
    """`in` rather than `or` — an explicit local `False` must override a
    user `True`. `or` short-circuits on False and silently falls back.
    """
    if "skipAutoPermissionPrompt" in local_data:
        return bool(local_data["skipAutoPermissionPrompt"])
    if "skipAutoPermissionPrompt" in user_data:
        return bool(user_data["skipAutoPermissionPrompt"])
    return False


def _warn_permission_collisions(
    allow: tuple[str, ...],
    deny: tuple[str, ...],
    *,
    path: Path,
    warnings: list[ScanWarning],
) -> None:
    """Emit a warning per rule that appears in both allow and deny.

    Claude Code's runtime resolution for this contradiction isn't
    documented; we surface the conflict and let the user resolve it.
    """
    for collision in sorted(set(allow) & set(deny)):
        warnings.append(
            ScanWarning(
                path=path,
                category="settings",
                reason=(
                    f"permission rule {collision!r} appears in both "
                    "allow and deny"
                ),
            )
        )


def _load_marketplaces(
    root: Path, warnings: list[ScanWarning]
) -> dict[str, dict[str, str]]:
    """Return {marketplace_name: source_dict} from all known registries.

    Sources, in increasing precedence (later wins on conflict):
    1. `<root>/plugins/known_marketplaces.json` — Claude Code's
       authoritative cache; its `source` block is what's cloned.
    2. `extraKnownMarketplaces` in settings.json — user-declared.
    3. `extraKnownMarketplaces` in remote-settings.json — enterprise.

    Each source_dict carries flattened `source.*` keys plus optional
    `installLocation` / `lastUpdated` from the registry file.
    """
    out: dict[str, dict[str, str]] = {}

    registry_path = root / "plugins" / "known_marketplaces.json"
    if registry_path.is_file():
        data, warning = load_json(registry_path, category="plugins")
        if warning is not None:
            warnings.append(warning)
        if isinstance(data, dict):
            for name, entry in cast("dict[str, object]", data).items():
                if isinstance(entry, dict):
                    out[str(name)] = _flatten_marketplace(
                        cast("dict[str, object]", entry)
                    )

    for filename in ("settings.json", "remote-settings.json"):
        data = _safe_load_json_dict(root / filename, "plugins", warnings)
        extra = as_dict(data.get("extraKnownMarketplaces"))
        if not extra:
            continue
        for name, entry in extra.items():
            if isinstance(entry, dict):
                flat = _flatten_marketplace(cast("dict[str, object]", entry))
                out.setdefault(str(name), {}).update(flat)

    return out


def _flatten_marketplace(entry: dict[str, object]) -> dict[str, str]:
    flat: dict[str, str] = {}
    src = entry.get("source")
    if isinstance(src, dict):
        for k, v in cast("dict[str, object]", src).items():
            if isinstance(v, str):
                flat[str(k)] = v
    for k in ("installLocation", "lastUpdated"):
        v = entry.get(k)
        if isinstance(v, str):
            flat[k] = v
    # Canonical schema stores autoUpdate as a bool; persist as "true"/"false"
    # to fit the Mapping[str, str] shape this dict already carries.
    au = entry.get("autoUpdate")
    if isinstance(au, bool):
        flat["autoUpdate"] = "true" if au else "false"
    return flat


def _load_policy_limits(root: Path, warnings: list[ScanWarning]) -> dict[str, str]:
    """Return {restriction_name: formatted-string} from
    `<root>/policy-limits.json`.

    The string includes both the allowed bool and any `message` /
    `text` field the enterprise admin attached. Format:
    `"allowed"` / `"denied"` / `"denied — <message>"` etc.
    Empty dict when the file is absent.
    """
    path = root / "policy-limits.json"
    if not path.is_file():
        return {}
    data, warning = load_json(path, category="settings")
    if warning is not None:
        warnings.append(warning)
    if not isinstance(data, dict):
        return {}
    restrictions = cast("dict[str, object]", data).get("restrictions")
    if not isinstance(restrictions, dict):
        return {}
    out: dict[str, str] = {}
    for name, entry in cast("dict[str, object]", restrictions).items():
        if not isinstance(entry, dict):
            continue
        entry_d = cast("dict[str, object]", entry)
        allowed = entry_d.get("allowed")
        verdict = (
            "allowed" if allowed is True else "denied" if allowed is False else "?"
        )
        message = as_str(entry_d.get("message")) or as_str(entry_d.get("text"))
        out[str(name)] = f"{verdict} — {message}" if message else verdict
    return out


def _parse_status_line(v: object) -> Mapping[str, str] | None:
    """Normalize statusLine to a {type, command, ...} dict, or None.

    Accepts dict-shaped configs (modern) and bare strings (legacy form
    where the entire value is the shell command). Non-string dict
    values (e.g. `padding: 1`, `refreshInterval: 30`) are coerced to
    str rather than dropped — they're real config the user wrote and
    inspecting them in agentpeek shouldn't silently lose them.
    """
    if isinstance(v, str):
        return MappingProxyType({"type": "command", "command": v})
    if isinstance(v, dict):
        d = {
            str(k): str(val)
            for k, val in cast("dict[str, object]", v).items()
        }
        return MappingProxyType(d) if d else None
    return None


def _load_blocklist(root: Path, warnings: list[ScanWarning]) -> dict[str, str]:
    """Return {qualified_id: reason} for plugins in `plugins/blocklist.json`.

    Claude Code refuses to load any plugin in this file regardless of
    enabledPlugins. The blocklist only lives at user scope; at project
    scope the file is absent and we return {}.
    """
    path = root / "plugins" / "blocklist.json"
    if not path.exists():
        return {}
    data, warning = load_json(path, category="plugins")
    if warning is not None:
        warnings.append(warning)
    if not isinstance(data, dict):
        return {}
    entries = cast("dict[str, object]", data).get("plugins")
    if not isinstance(entries, list):
        return {}
    out: dict[str, str] = {}
    for entry in cast("list[object]", entries):
        if not isinstance(entry, dict):
            continue
        entry_d = cast("dict[str, object]", entry)
        qualified_id = as_str(entry_d.get("plugin"))
        if not qualified_id:
            continue
        reason = as_str(entry_d.get("reason"))
        text = as_str(entry_d.get("text"))
        if reason and text and reason != text:
            warnings.append(
                ScanWarning(
                    path=path,
                    category="plugins",
                    reason=(
                        f"blocklist entry for {qualified_id} has both `reason` and "
                        f"`text` with different values; surfacing both"
                    ),
                )
            )
            out[qualified_id] = f"{reason} (text: {text})"
        else:
            out[qualified_id] = reason or text or ""
    return out


def _collect_enabled_plugins(root: Path, warnings: list[ScanWarning]) -> set[str]:
    # All three files contribute to enabledPlugins. `/plugin install` writes
    # to settings.local.json; enterprise/remote config lands in
    # remote-settings.json; settings.json is the user-committed default.
    maps: dict[str, dict[str, object]] = {}
    for filename in ("settings.json", "settings.local.json", "remote-settings.json"):
        data = _safe_load_json_dict(root / filename, "plugins", warnings)
        enabled_map = as_dict(data.get("enabledPlugins"))
        if enabled_map is not None:
            maps[filename] = enabled_map

    # Note when two source files disagree on the same qualified id.
    # Claude Code's observed runtime behavior unions enabledPlugins
    # across these files (a plugin enabled in ANY file is loaded), so
    # a disagreement isn't a misconfiguration — the warning text reflects that.
    filenames = list(maps)
    for i, a in enumerate(filenames):
        for b in filenames[i + 1 :]:
            for qualified_id in set(maps[a]) & set(maps[b]):
                if bool(maps[a][qualified_id]) != bool(maps[b][qualified_id]):
                    effective = bool(maps[a][qualified_id]) or bool(maps[b][qualified_id])
                    warnings.append(
                        ScanWarning(
                            path=None,
                            category="plugins",
                            reason=(
                                f"plugin {qualified_id} enabledPlugins value differs: "
                                f"{a}={bool(maps[a][qualified_id])}, "
                                f"{b}={bool(maps[b][qualified_id])}; "
                                f"Claude Code unions across files, "
                                f"effective={effective}"
                            ),
                        )
                    )

    merged: dict[str, object] = {}
    for enabled_map in maps.values():
        merged.update(enabled_map)
    return {str(k) for k, v in merged.items() if bool(v)}


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
        body = _safe_read_text(path)
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
