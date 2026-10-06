"""Shared runtime bound for adjudicating distinct immutable chunk candidates.

A changed candidate may need a new evidence-based review after semantic repair.
Repeating the route for an unchanged candidate is not new evidence. This budget
is local to one bounded chunk lifecycle and never rewrites persisted receipts.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_ADJUDICATION_CANDIDATES = 3


class AdjudicationBudget:
    """Allow at most three route calls, each for a distinct candidate digest."""

    def __init__(self) -> None:
        self._claimed: set[str] = set()

    @property
    def claimed_count(self) -> int:
        return len(self._claimed)

    @staticmethod
    def _identity(candidate: Any) -> str:
        encoded = json.dumps(candidate, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()

    def can_claim(self, candidate: Any) -> bool:
        """Check eligibility without reserving a slot; claim again before await."""
        identity = self._identity(candidate)
        return identity not in self._claimed and len(self._claimed) < MAX_ADJUDICATION_CANDIDATES

    def claim(self, candidate: Any) -> bool:
        """Reserve one invocation before starting the shared adjudication route.

        A rejected claim consumes no slot. A successful claim consumes its slot
        even if the route later fails; this does not authorize retrying identical
        evidence or turn an uncertain result into approval.
        """
        identity = self._identity(candidate)
        if identity in self._claimed or len(self._claimed) >= MAX_ADJUDICATION_CANDIDATES:
            return False
        self._claimed.add(identity)
        return True
