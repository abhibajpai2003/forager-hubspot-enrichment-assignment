"""Shared HTTP retry policy for the Forager and HubSpot clients."""

from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 30.0


class _RetryableResponseError(Exception):
    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"HTTP {response.status_code}")
        self.response = response


def _wait(base_wait: float) -> Any:
    backoff = wait_exponential(multiplier=base_wait, max=20)

    def wait(state: RetryCallState) -> float:
        exc = state.outcome.exception() if state.outcome else None
        if isinstance(exc, _RetryableResponseError):
            retry_after = exc.response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                return min(float(retry_after), MAX_RETRY_AFTER_SECONDS)
        return float(backoff(state))

    return wait


async def send_with_retries(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    attempts: int = 4,
    base_wait: float = 0.5,
    **kwargs: Any,
) -> httpx.Response:
    """Send a request, retrying network errors, 429 and 5xx with backoff.

    Returns the final response, which may still be an error response; callers decide what an
    error status means for them.
    """
    retrying = AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=_wait(base_wait),
        retry=retry_if_exception_type((_RetryableResponseError, httpx.TransportError)),
        reraise=True,
    )
    try:
        async for attempt in retrying:
            with attempt:
                response = await client.request(method, url, **kwargs)
                if response.status_code in RETRY_STATUSES:
                    raise _RetryableResponseError(response)
                return response
    except _RetryableResponseError as exc:
        return exc.response
    raise AssertionError("unreachable")  # pragma: no cover
