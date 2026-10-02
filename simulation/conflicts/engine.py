from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConflictFinding:
    kind: str
    severity: str
    details: dict[str, object]

    @property
    def fingerprint(self) -> str:
        value = json.dumps(
            {"kind": self.kind, "details": self.details}, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(value.encode()).hexdigest()
