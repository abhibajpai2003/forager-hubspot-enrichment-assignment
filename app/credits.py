"""Forager credit accounting.

Every paid Forager call reserves its cost from a fixed budget before it is sent, so concurrent
calls can never overshoot the budget together. The ledger is saved to disk so the count
survives restarts (mount a volume on Railway).
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Credits per request, from https://docs.forager.ai/api-overview/credit-pricing
CREDIT_COST: dict[str, int] = {
    "organization_search": 1,
    "person_role_search": 1,
    "reverse_email_lookup": 5,
    "work_email": 5,  # charged only when an address is found
    "phone_numbers": 15,
    "totals": 0,
}


class CreditBudgetExceededError(Exception):
    def __init__(self, operation: str, cost: int, remaining: int) -> None:
        super().__init__(
            f"Forager call '{operation}' needs {cost} credits but only {remaining} remain "
            "in FORAGER_CREDIT_BUDGET"
        )
        self.operation = operation
        self.cost = cost
        self.remaining = remaining


@dataclass
class Charge:
    """Handed to the caller of `CreditLedger.spend`; set `cost` to what was actually spent."""

    cost: int


class CreditLedger:
    def __init__(self, budget: int, path: Path | None = None) -> None:
        self.budget = budget
        self._path = path
        self._lock = asyncio.Lock()
        self._reserved = 0
        self._entries: list[dict[str, Any]] = self._load()

    @property
    def spent(self) -> int:
        return sum(int(e["cost"]) for e in self._entries)

    @property
    def remaining(self) -> int:
        return max(self.budget - self.spent - self._reserved, 0)

    def summary(self, recent: int = 20) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "spent": self.spent,
            "remaining": self.remaining,
            "recent": self._entries[-recent:],
        }

    @asynccontextmanager
    async def spend(self, operation: str, cost: int, detail: str = "") -> AsyncIterator[Charge]:
        """Reserve `cost` credits for one call and record what it actually cost.

        If the call raises, nothing is recorded: Forager does not charge failed requests.
        """
        async with self._lock:
            available = self.budget - self.spent - self._reserved
            if cost > available:
                raise CreditBudgetExceededError(operation, cost, max(available, 0))
            self._reserved += cost

        charge = Charge(cost=cost)
        completed = False
        try:
            yield charge
            completed = True
        finally:
            async with self._lock:
                self._reserved -= cost
                if completed and charge.cost > 0:
                    entry = {
                        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "operation": operation,
                        "cost": charge.cost,
                        "detail": detail,
                    }
                    self._entries.append(entry)
                    self._save()
                    log.info(
                        "forager_credits_spent",
                        operation=operation,
                        cost=charge.cost,
                        spent=self.spent,
                        budget=self.budget,
                    )

    def _load(self) -> list[dict[str, Any]]:
        if self._path is None or not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text())
        except (OSError, ValueError):
            log.warning("credit_ledger_unreadable", path=str(self._path))
            return []
        return list(data.get("entries", []))

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"entries": self._entries}, indent=2))
            tmp.replace(self._path)
        except OSError as exc:
            # Spending is still tracked in memory; only the restart-proof copy is lost.
            log.error("credit_ledger_write_failed", path=str(self._path), error=str(exc))
