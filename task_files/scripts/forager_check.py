"""Forager checks to run before the demo.

    # Which account IDs does my key have, and how many credits are left? (free)
    uv run python -m scripts.forager_check accounts

    # How many current employees match each title? (totals endpoint, expected free)
    uv run python -m scripts.forager_check totals --domain openai.com \
        --titles "Account Executive" "Recruiter" "Solutions Engineer"

    # Who would the finder pick, and what does Forager return for them? (1 credit)
    uv run python -m scripts.forager_check preview --domain openai.com \
        --titles "Account Executive" "Recruiter"

`totals` prints the balance before and after, so you can confirm it really is free.
`preview` shows the HubSpot fields each person would get, without writing anything.
"""

import argparse
import asyncio
import json

from app.cache import ResponseCache
from app.clients.forager import ForagerClient, role_search_payload, title_query
from app.config import get_settings
from app.credits import CreditLedger
from app.mapping import contact_properties


def build_client(account_id: int, budget: int = 0) -> ForagerClient:
    settings = get_settings()
    if settings.forager_api_key is None:
        raise SystemExit("FORAGER_API_KEY is not set")
    return ForagerClient(
        base_url=settings.forager_base_url,
        api_key=settings.forager_api_key.get_secret_value(),
        account_id=account_id,
        ledger=CreditLedger(budget=budget),
        cache=ResponseCache(None),
    )


def account_id() -> int:
    settings = get_settings()
    if settings.forager_account_id is None:
        raise SystemExit("FORAGER_ACCOUNT_ID is not set (find it with the 'accounts' command)")
    return settings.forager_account_id


async def accounts() -> None:
    forager = build_client(account_id=0)
    try:
        for account in await forager.accounts():
            print(
                f"account id {account['id']}: {account['name']!r}, "
                f"credits balance {account['credits_balance']}"
            )
    finally:
        await forager.aclose()


async def totals(domain: str, titles: list[str]) -> None:
    forager = build_client(account_id())
    try:
        before = await forager.credits_balance()
        print(f"Balance before: {before}")
        for title in titles:
            counts = await forager.person_role_search_totals(
                role_search_payload(organization_domains=[domain], role_title=title_query([title]))
            )
            print(f"  {title:<30} {counts.get('total_persons', 0)} people")
        combined = await forager.person_role_search_totals(
            role_search_payload(organization_domains=[domain], role_title=title_query(titles))
        )
        print(f"  {'(all titles combined)':<30} {combined.get('total_persons', 0)} people")
        after = await forager.credits_balance()
        print(f"Balance after:  {after}")
        if before != after:
            print("WARNING: totals calls changed the balance; they are not free.")
    finally:
        await forager.aclose()


async def preview(domain: str, titles: list[str]) -> None:
    forager = build_client(account_id(), budget=1)  # exactly one role search
    try:
        roles = await forager.person_role_search(
            role_search_payload(organization_domains=[domain], role_title=title_query(titles))
        )
        print(f"{len(roles)} results (1 credit spent)\n")
        for role in roles:
            if role.person is None:
                continue
            props = contact_properties(role.person, role, role.organization)
            print(json.dumps(props, indent=2))
            raw_location = role.person.location.model_dump() if role.person.location else None
            print(f"raw location: {json.dumps(raw_location)}\n")
        print(f"Balance now: {await forager.credits_balance()}")
    finally:
        await forager.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("accounts", help="list account IDs and credit balances (free)")
    for name, help_text in [
        ("totals", "count matching people per job title (free)"),
        ("preview", "list matching people and their mapped fields (1 credit)"),
    ]:
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--domain", required=True)
        command.add_argument("--titles", nargs="+", required=True)
    args = parser.parse_args()

    if args.command == "accounts":
        asyncio.run(accounts())
    elif args.command == "totals":
        asyncio.run(totals(args.domain, args.titles))
    else:
        asyncio.run(preview(args.domain, args.titles))


if __name__ == "__main__":
    main()
