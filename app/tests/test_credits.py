import asyncio
from pathlib import Path

import pytest

from app.credits import CreditBudgetExceededError, CreditLedger


async def test_spend_records_cost() -> None:
    ledger = CreditLedger(budget=10)
    async with ledger.spend("work_email", 5):
        pass
    assert ledger.spent == 5
    assert ledger.remaining == 5


async def test_refuses_call_over_budget() -> None:
    ledger = CreditLedger(budget=4)
    with pytest.raises(CreditBudgetExceededError):
        async with ledger.spend("work_email", 5):
            pass
    assert ledger.spent == 0


async def test_failed_call_is_not_charged() -> None:
    ledger = CreditLedger(budget=10)
    with pytest.raises(RuntimeError):
        async with ledger.spend("person_role_search", 1):
            raise RuntimeError("boom")
    assert ledger.spent == 0
    assert ledger.remaining == 10


async def test_caller_can_lower_actual_cost() -> None:
    ledger = CreditLedger(budget=10)
    async with ledger.spend("work_email", 5) as charge:
        charge.cost = 0
    assert ledger.spent == 0


async def test_concurrent_calls_cannot_overshoot() -> None:
    ledger = CreditLedger(budget=12)
    started = 0
    refused = 0

    async def call() -> None:
        nonlocal started, refused
        try:
            async with ledger.spend("work_email", 5):
                started += 1
                await asyncio.sleep(0.01)
        except CreditBudgetExceededError:
            refused += 1

    await asyncio.gather(*(call() for _ in range(4)))
    assert started == 2
    assert refused == 2
    assert ledger.spent == 10


async def test_ledger_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    ledger = CreditLedger(budget=45, path=path)
    async with ledger.spend("organization_search", 1, detail="openai.com"):
        pass
    reloaded = CreditLedger(budget=45, path=path)
    assert reloaded.spent == 1
    assert reloaded.summary()["recent"][0]["operation"] == "organization_search"
