"""Create the custom HubSpot properties this service writes, and check the standard ones exist.

Safe to re-run: existing properties are left alone.

    uv run python -m scripts.setup_hubspot_properties
"""

import asyncio
import sys

from app.clients.hubspot import HubSpotClient
from app.config import get_settings
from app.properties import (
    COMPANY_CUSTOM,
    COMPANY_STANDARD,
    CONTACT_CUSTOM,
    CONTACT_STANDARD,
    GROUP_LABEL,
    GROUP_NAME,
)

OBJECTS = {
    "contacts": (CONTACT_CUSTOM, CONTACT_STANDARD),
    "companies": (COMPANY_CUSTOM, COMPANY_STANDARD),
}


async def main() -> int:
    settings = get_settings()
    if settings.hubspot_access_token is None:
        print("HUBSPOT_ACCESS_TOKEN is not set", file=sys.stderr)
        return 1
    hubspot = HubSpotClient(
        access_token=settings.hubspot_access_token.get_secret_value(),
        base_url=settings.hubspot_base_url,
    )
    missing_standard: list[str] = []
    try:
        for object_type, (custom, standard) in OBJECTS.items():
            created = await hubspot.create_property_group(object_type, GROUP_NAME, GROUP_LABEL)
            print(f"{object_type}: group '{GROUP_NAME}' {'created' if created else 'exists'}")
            for definition in custom:
                created = await hubspot.create_property(object_type, definition)
                print(
                    f"{object_type}: {definition['name']:<28} {'created' if created else 'exists'}"
                )
            for name in standard:
                if await hubspot.get_property(object_type, name) is None:
                    missing_standard.append(f"{object_type}.{name}")
    finally:
        await hubspot.aclose()

    if missing_standard:
        print("\nStandard properties not found in this portal (values for them will fail):")
        for name in missing_standard:
            print(f"  - {name}")
        return 1
    print("\nAll standard properties exist.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
