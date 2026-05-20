from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import frontmatter
import yaml

from agentpeek.models import ScanWarning


@dataclass(frozen=True, slots=True)
class FrontmatterFile:
    metadata: Mapping[str, object]
    body: str


def read_str_field(
    metadata: Mapping[str, object],
    key: str,
    *,
    path: Path,
    category: str,
    warnings: list[ScanWarning],
) -> str | None:
    """Read a string-typed frontmatter field; warn if present but
    not a string.

    Falling back to the directory/file name without warning hides
    misconfiguration — the user typed `name: ["foo"]` and gets a
    different name than they expect with no explanation. Returns
    None when the key is absent or the value isn't a string.
    """
    v = metadata.get(key)
    if v is None:
        return None
    if isinstance(v, str):
        return v
    warnings.append(
        ScanWarning(
            path=path,
            category=category,
            reason=(
                f"frontmatter `{key}` is {type(v).__name__}, "
                "expected string; ignored"
            ),
        )
    )
    return None


def read_string_list_field(
    metadata: Mapping[str, object],
    key: str,
    *,
    path: Path,
    category: str,
    warnings: list[ScanWarning],
) -> tuple[str, ...]:
    """Read a field that may be either a comma-separated string or a YAML list.

    Claude Code accepts both forms for `allowed-tools` / `disallowed-tools`
    in skill, agent, and command frontmatter — production plugins use either
    style. Returns the items as a tuple; () when absent. Warns only on a
    genuinely unexpected type (number, mapping, etc.).
    """
    v = metadata.get(key)
    if v is None:
        return ()
    if isinstance(v, str):
        return tuple(s.strip() for s in v.split(",") if s.strip())
    if isinstance(v, list):
        items: list[str] = []
        for item in cast("list[object]", v):
            s = str(item).strip()
            if s:
                items.append(s)
        return tuple(items)
    warnings.append(
        ScanWarning(
            path=path,
            category=category,
            reason=(
                f"frontmatter `{key}` is {type(v).__name__}, "
                "expected string or list; ignored"
            ),
        )
    )
    return ()


def read_str_or_list_field(
    metadata: Mapping[str, object],
    key: str,
    *,
    path: Path,
    category: str,
    warnings: list[ScanWarning],
) -> str | None:
    """Read a string field that tolerates a YAML list value, joined with ' '.

    `argument-hint` in slash command frontmatter is typically a single hint
    string (`"<file> [options]"`) but YAML list form (`["detail"]`) also
    appears in real configs.
    """
    v = metadata.get(key)
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        items = [str(item).strip() for item in cast("list[object]", v)]
        joined = " ".join(s for s in items if s)
        return joined or None
    warnings.append(
        ScanWarning(
            path=path,
            category=category,
            reason=(
                f"frontmatter `{key}` is {type(v).__name__}, "
                "expected string or list; ignored"
            ),
        )
    )
    return None


def load_frontmatter(
    path: Path, *, category: str
) -> tuple[FrontmatterFile | None, ScanWarning | None]:
    # `utf-8-sig` consumes a leading BOM (U+FEFF) if present and is
    # equivalent to plain `utf-8` otherwise.
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None, ScanWarning(
            path=path, category=category, reason=f"file not found: {path}"
        )
    except UnicodeDecodeError as e:
        return None, ScanWarning(
            path=path,
            category=category,
            reason=f"could not decode utf-8: {e.reason}",
        )
    except OSError as e:
        return None, ScanWarning(
            path=path, category=category, reason=f"could not read: {e.strerror}"
        )

    try:
        post = frontmatter.loads(text)
    except yaml.YAMLError as e:
        return None, ScanWarning(
            path=path, category=category, reason=f"invalid frontmatter YAML: {e}"
        )
    except Exception as e:
        return None, ScanWarning(
            path=path, category=category, reason=f"unexpected error: {e!r}"
        )

    # python-frontmatter normalizes any non-mapping YAML (scalars, lists,
    # null) to an empty dict before we see it, so the cast here is safe.
    metadata = cast("Mapping[str, object]", dict(post.metadata))  # type: ignore[reportUnknownArgumentType]
    body = cast("str", post.content)  # type: ignore[reportUnknownMemberType]
    return FrontmatterFile(metadata=metadata, body=body), None
