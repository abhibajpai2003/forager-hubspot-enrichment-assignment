"""On-disk cache of Forager responses, so repeating a lookup never costs credits twice."""

import hashlib
import json
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class ResponseCache:
    """Stores one JSON file per (operation, payload). `None` responses are cached too."""

    def __init__(self, directory: Path | None) -> None:
        self._dir = directory

    @staticmethod
    def key(operation: str, payload: Any) -> str:
        canonical = json.dumps({"op": operation, "payload": payload}, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def get(self, operation: str, payload: Any) -> tuple[bool, Any]:
        """Return `(hit, response)`."""
        if self._dir is None:
            return False, None
        path = self._dir / f"{self.key(operation, payload)}.json"
        if not path.exists():
            return False, None
        try:
            return True, json.loads(path.read_text())["response"]
        except (OSError, ValueError, KeyError):
            log.warning("forager_cache_unreadable", path=str(path))
            return False, None

    def set(self, operation: str, payload: Any, response: Any) -> None:
        if self._dir is None:
            return
        path = self._dir / f"{self.key(operation, payload)}.json"
        record = {"operation": operation, "payload": payload, "response": response}
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record, indent=2))
        except OSError as exc:
            log.error("forager_cache_write_failed", path=str(path), error=str(exc))
