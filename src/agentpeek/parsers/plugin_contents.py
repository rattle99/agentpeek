"""Parse the on-disk contents of a single plugin installation.

A plugin install path contains some subset of:

- `.claude-plugin/plugin.json` — manifest with description, version, author,
  homepage, license, keywords, optionally `mcpServers`.
- `skills/<name>/SKILL.md` — skills with frontmatter (name + description).
- `agents/<name>.md` — agents with frontmatter.
- `commands/**/*.md` — slash commands with frontmatter (description,
  argument-hint, allowed-tools).
- `hooks/hooks.json` — hook configuration (same shape as settings.json hooks).
- `.mcp.json` — MCP server definitions (same shape as settings.json mcpServers).

Per-file failures emit `ScanWarning` entries; the parser never raises.
Returned `SlashCommand` / `HookSpec` / `MCPServer` instances carry
`source_plugin = qualified_id` for provenance.
"""

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import cast

from agentpeek.models import (
    HookSpec,
    MCPServer,
    PluginAgent,
    PluginManifest,
    PluginSkill,
    ScanWarning,
    SlashCommand,
)
from agentpeek.parsers import load_frontmatter, load_json


@dataclass(frozen=True, slots=True)
class PluginContents:
    manifest: PluginManifest | None
    skills: tuple[PluginSkill, ...]
    agents: tuple[PluginAgent, ...]
    commands: tuple[SlashCommand, ...]
    hooks: tuple[HookSpec, ...]
    mcps: tuple[MCPServer, ...]
    warnings: tuple[ScanWarning, ...]


def parse_plugin_contents(
    install_path: Path, *, qualified_id: str
) -> PluginContents:
    """Enumerate everything inside one plugin install_path.

    `qualified_id` is stamped onto every returned item via its
    `source_plugin` field so downstream renderers can show provenance.
    """
    warnings: list[ScanWarning] = []
    manifest_data, manifest = _parse_manifest(install_path, warnings)
    return PluginContents(
        manifest=manifest,
        skills=_parse_skills(
            install_path / "skills", warnings, qualified_id=qualified_id
        ),
        agents=_parse_agents(
            install_path / "agents", warnings, qualified_id=qualified_id
        ),
        commands=_parse_commands(
            install_path / "commands", warnings, qualified_id=qualified_id
        ),
        hooks=_parse_hooks(
            install_path / "hooks" / "hooks.json",
            warnings,
            qualified_id=qualified_id,
        ),
        mcps=_parse_mcps(
            install_path, manifest_data, warnings, qualified_id=qualified_id
        ),
        warnings=tuple(warnings),
    )


# --- Manifest -----------------------------------------------------------


def _parse_manifest(
    install_path: Path, warnings: list[ScanWarning]
) -> tuple[dict[str, object] | None, PluginManifest | None]:
    """Returns (raw manifest dict, parsed manifest).

    Raw dict is also returned so `_parse_mcps` can read `mcpServers`
    from it without re-reading the file.
    """
    path = install_path / ".claude-plugin" / "plugin.json"
    if not path.is_file():
        return None, None
    data, warning = load_json(path, category="plugin_manifest")
    if warning is not None:
        warnings.append(warning)
    if not isinstance(data, dict):
        return None, None
    d = cast("dict[str, object]", data)
    author = d.get("author")
    author_name: str | None = None
    author_email: str | None = None
    if isinstance(author, dict):
        a = cast("dict[str, object]", author)
        author_name = _as_str(a.get("name"))
        author_email = _as_str(a.get("email"))
    elif isinstance(author, str):
        author_name = author
    keywords_raw = d.get("keywords")
    keywords: tuple[str, ...] = (
        tuple(s for s in cast("list[object]", keywords_raw) if isinstance(s, str))
        if isinstance(keywords_raw, list)
        else ()
    )
    manifest = PluginManifest(
        description=_as_str(d.get("description")),
        version=_as_str(d.get("version")),
        author_name=author_name,
        author_email=author_email,
        homepage=_as_str(d.get("homepage")),
        license=_as_str(d.get("license")),
        keywords=keywords,
    )
    return d, manifest


# --- Skills / Agents (frontmatter-bearing single-file resources) ---------


def _parse_skills(
    skills_dir: Path, warnings: list[ScanWarning], *, qualified_id: str
) -> tuple[PluginSkill, ...]:
    if not skills_dir.is_dir():
        return ()
    results: list[PluginSkill] = []
    for sub in sorted(skills_dir.iterdir()):
        if not sub.is_dir():
            continue
        skill_md = sub / "SKILL.md"
        if not skill_md.is_file():
            continue
        file, warning = load_frontmatter(skill_md, category="plugin_skill")
        if warning is not None:
            warnings.append(warning)
        if file is None:
            continue
        results.append(
            PluginSkill(
                path=skill_md,
                name=_as_str(file.metadata.get("name")) or sub.name,
                description=_as_str(file.metadata.get("description")),
                body=file.body,
                source_plugin=qualified_id,
            )
        )
    return tuple(results)


def _parse_agents(
    agents_dir: Path, warnings: list[ScanWarning], *, qualified_id: str
) -> tuple[PluginAgent, ...]:
    if not agents_dir.is_dir():
        return ()
    results: list[PluginAgent] = []
    for md in sorted(agents_dir.glob("*.md")):
        file, warning = load_frontmatter(md, category="plugin_agent")
        if warning is not None:
            warnings.append(warning)
        if file is None:
            continue
        results.append(
            PluginAgent(
                path=md,
                name=_as_str(file.metadata.get("name")) or md.stem,
                description=_as_str(file.metadata.get("description")),
                body=file.body,
                source_plugin=qualified_id,
            )
        )
    return tuple(results)


# --- Slash commands ------------------------------------------------------


def _parse_commands(
    commands_dir: Path,
    warnings: list[ScanWarning],
    *,
    qualified_id: str,
) -> tuple[SlashCommand, ...]:
    if not commands_dir.is_dir():
        return ()
    results: list[SlashCommand] = []
    for md in sorted(commands_dir.rglob("*.md")):
        file, warning = load_frontmatter(md, category="plugin_command")
        if warning is not None:
            warnings.append(warning)
        name = str(md.relative_to(commands_dir).with_suffix("")).replace("\\", "/")
        if file is None:
            try:
                raw = md.read_text(encoding="utf-8")
            except OSError:
                raw = ""
            results.append(
                SlashCommand(
                    path=md,
                    name=name,
                    description=None,
                    argument_hint=None,
                    allowed_tools=(),
                    body=raw,
                    source_plugin=qualified_id,
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
                path=md,
                name=name,
                description=_as_str(file.metadata.get("description")),
                argument_hint=_as_str(file.metadata.get("argument-hint")),
                allowed_tools=allowed_tuple,
                body=file.body,
                source_plugin=qualified_id,
            )
        )
    return tuple(results)


# --- Hooks --------------------------------------------------------------


def _parse_hooks(
    hooks_path: Path,
    warnings: list[ScanWarning],
    *,
    qualified_id: str,
) -> tuple[HookSpec, ...]:
    if not hooks_path.is_file():
        return ()
    data, warning = load_json(hooks_path, category="plugin_hook")
    if warning is not None:
        warnings.append(warning)
    if not isinstance(data, dict):
        return ()
    d = cast("dict[str, object]", data)
    # Plugin hooks.json may wrap entries in a top-level `hooks` key or
    # place event keys at the root. Support both.
    inner = d.get("hooks")
    events: dict[str, object] = (
        cast("dict[str, object]", inner) if isinstance(inner, dict) else d
    )
    results: list[HookSpec] = []
    for event, raw_entries in events.items():
        if not isinstance(raw_entries, list):
            continue
        for entry in cast("list[object]", raw_entries):
            if not isinstance(entry, dict):
                continue
            entry_d = cast("dict[str, object]", entry)
            matcher = _as_str(entry_d.get("matcher"))
            inner_list = entry_d.get("hooks")
            if not isinstance(inner_list, list):
                continue
            for inner_h in cast("list[object]", inner_list):
                if not isinstance(inner_h, dict):
                    continue
                inner_d = cast("dict[str, object]", inner_h)
                cmd = _as_str(inner_d.get("command"))
                if cmd is None:
                    continue
                results.append(
                    HookSpec(
                        event=event,
                        matcher=matcher,
                        type=_as_str(inner_d.get("type")) or "command",
                        command=cmd,
                        timeout=_as_int(inner_d.get("timeout")),
                        # Plugin hooks reference scripts inside the plugin
                        # install path; we don't currently resolve those (the
                        # existing _scan_hooks resolves against the root,
                        # which here is the plugin dir). Leave None for now.
                        referenced_script=None,
                        script_exists=False,
                        source_plugin=qualified_id,
                    )
                )
    return tuple(results)


# --- MCP servers --------------------------------------------------------


def _parse_mcps(
    install_path: Path,
    manifest_data: dict[str, object] | None,
    warnings: list[ScanWarning],
    *,
    qualified_id: str,
) -> tuple[MCPServer, ...]:
    """Read MCP servers from `<install>/.mcp.json` and/or the manifest's
    `mcpServers` key. Duplicates by name resolve to the .mcp.json one.
    """
    candidates: list[tuple[Path, dict[str, object]]] = []
    if manifest_data is not None:
        manifest_servers = manifest_data.get("mcpServers")
        if isinstance(manifest_servers, dict):
            candidates.append(
                (
                    install_path / ".claude-plugin" / "plugin.json",
                    cast("dict[str, object]", manifest_servers),
                )
            )
    mcp_path = install_path / ".mcp.json"
    if mcp_path.is_file():
        data, warning = load_json(mcp_path, category="plugin_mcp")
        if warning is not None:
            warnings.append(warning)
        if isinstance(data, dict):
            data_d = cast("dict[str, object]", data)
            servers = data_d.get("mcpServers", data_d)
            if isinstance(servers, dict):
                candidates.append((mcp_path, cast("dict[str, object]", servers)))
    seen: dict[str, MCPServer] = {}
    for source_path, servers in candidates:
        for srv_name, srv in servers.items():
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
            seen[str(srv_name)] = MCPServer(
                name=str(srv_name),
                source_path=source_path,
                command=_as_str(srv_d.get("command")),
                args=args_tuple,
                env=MappingProxyType(env_obj),
                source_plugin=qualified_id,
            )
    return tuple(seen[k] for k in sorted(seen))


# --- Local helpers ------------------------------------------------------


def _as_str(v: object) -> str | None:
    return v if isinstance(v, str) else None


def _as_int(v: object) -> int | None:
    if isinstance(v, bool):
        return None
    return v if isinstance(v, int) else None


def _as_str_dict(v: object) -> dict[str, str]:
    if isinstance(v, dict):
        return {
            str(k): str(val)
            for k, val in cast("dict[str, object]", v).items()
            if isinstance(val, str)
        }
    return {}
