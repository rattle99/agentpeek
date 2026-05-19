"""Type coercion helpers for JSON-derived values.

Claude Code's on-disk JSON is user-edited, so any field can be missing
or wrong-typed. These helpers reject the wrong type rather than coerce
through `str(...)` — silent coercion masks malformed config.
"""

from typing import cast


def as_str(v: object) -> str | None:
    return v if isinstance(v, str) else None


def as_int(v: object) -> int | None:
    if isinstance(v, bool):
        return None
    return v if isinstance(v, int) else None


def as_dict(v: object) -> dict[str, object] | None:
    if isinstance(v, dict):
        return cast("dict[str, object]", v)
    return None


def as_str_dict(v: object) -> dict[str, str]:
    if isinstance(v, dict):
        return {
            str(k): str(val)
            for k, val in cast("dict[str, object]", v).items()
            if isinstance(val, str)
        }
    return {}


def as_str_tuple(v: object) -> tuple[str, ...]:
    if isinstance(v, list):
        return tuple(s for s in cast("list[object]", v) if isinstance(s, str))
    return ()
