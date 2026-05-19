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
