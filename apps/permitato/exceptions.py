"""Permitato exception lifecycle — domain-family regex, TTL, persistence."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_DOMAIN_RE = re.compile(r"^[\w][\w.-]*\.[\w]{2,}$")


def build_domain_regex(domain: str) -> str:
    """Build a Pi-hole regex that matches domain and all subdomains."""
    domain = domain.strip().lower()
    if not domain or "." not in domain or not _DOMAIN_RE.match(domain):
        raise ValueError(f"Invalid domain: {domain!r}")
    escaped = re.escape(domain)
    return rf"(^|\.){escaped}$"


@dataclass
class DomainException:
    id: str
    domain: str
    regex_pattern: str
    reason: str
    granted_at: float
    expires_at: float
    ttl_seconds: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "domain": self.domain,
            "regex_pattern": self.regex_pattern,
            "reason": self.reason,
            "granted_at": self.granted_at,
            "expires_at": self.expires_at,
            "ttl_seconds": self.ttl_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DomainException:
        return cls(**{k: data[k] for k in (
            "id", "domain", "regex_pattern", "reason",
            "granted_at", "expires_at", "ttl_seconds",
        )})


@dataclass
class ExceptionStore:
    data_dir: Path
    _exceptions: dict[str, DomainException] = field(default_factory=dict)

    def grant(self, domain: str, reason: str, ttl_seconds: int = 3600) -> DomainException:
        now = time.time()
        exc = DomainException(
            id=str(uuid.uuid4()),
            domain=domain.strip().lower(),
            regex_pattern=build_domain_regex(domain),
            reason=reason,
            granted_at=now,
            expires_at=now + ttl_seconds,
            ttl_seconds=ttl_seconds,
        )
        self._exceptions[exc.id] = exc
        return exc

    def revoke(self, exception_id: str) -> DomainException:
        if exception_id not in self._exceptions:
            raise KeyError(f"No exception with id: {exception_id}")
        return self._exceptions.pop(exception_id)

    def get_expired(self) -> list[DomainException]:
        """Return expired exceptions without removing them."""
        now = time.time()
        return [exc for exc in self._exceptions.values() if exc.expires_at <= now]

    def cleanup_expired(self) -> list[str]:
        now = time.time()
        expired_ids = [eid for eid, exc in self._exceptions.items() if exc.expires_at <= now]
        for eid in expired_ids:
            del self._exceptions[eid]
        return expired_ids

    def active_count(self) -> int:
        return len(self._exceptions)

    def list_active(self) -> list[dict]:
        return [exc.to_dict() for exc in self._exceptions.values()]

    def persist(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / "exceptions.json"
        data = {
            "version": 1,
            "exceptions": {eid: exc.to_dict() for eid, exc in self._exceptions.items()},
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load(self) -> None:
        path = self.data_dir / "exceptions.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for eid, exc_data in data.get("exceptions", {}).items():
                self._exceptions[eid] = DomainException.from_dict(exc_data)
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("Failed to load exceptions from %s, starting fresh", path)
            self._exceptions.clear()
