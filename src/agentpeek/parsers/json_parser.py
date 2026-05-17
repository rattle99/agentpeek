import json
from pathlib import Path

from agentpeek.models import ScanWarning


def load_json(path: Path, *, category: str) -> tuple[object | None, ScanWarning | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, ScanWarning(
            path=path, category=category, reason=f"file not found: {path}"
        )
    except json.JSONDecodeError as e:
        return None, ScanWarning(
            path=path,
            category=category,
            reason=f"invalid JSON at line {e.lineno} col {e.colno}: {e.msg}",
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
    except Exception as e:
        return None, ScanWarning(
            path=path, category=category, reason=f"unexpected error: {e!r}"
        )
