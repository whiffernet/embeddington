"""Shared retry policy for the startup reachability probes.

Both fatal startup probes — Qdrant and the /embed endpoint — need the same
behaviour: ride out a dependency that is restarting, fail immediately on a
misconfiguration, and bound total wall-clock so startup cannot outlast the
MCP client's own initialization timeout.

They live on the same host in the common deployment, so a host restart takes
both down at once. Giving only one of them a retry would make that retry
pointless: the other would still abort startup on the first attempt.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

import httpx

logger = logging.getLogger("embeddington.probe")

# Transport failures worth retrying: the request either never reached a server
# or the server never answered, so a later attempt can legitimately succeed.
#
# Deliberately NARROWER than httpx.HTTPError, which also covers permanent
# misconfigurations that no amount of retrying can fix — UnsupportedProtocol
# (a typo'd scheme such as `htp://host:6333`), TooManyRedirects, DecodingError
# and ProtocolError. Retrying those would only burn the startup budget before
# reporting a failure that was knowable on the first attempt.
RETRYABLE_TRANSPORT_ERRORS = (httpx.TimeoutException, httpx.NetworkError)


async def probe_with_retry(
    attempt: Callable[[], Awaitable[tuple[bool, str]]],
    *,
    target: str,
    what: str,
    retries: int = 0,
    backoff: float = 1.0,
    deadline: Optional[float] = None,
) -> tuple[bool, str]:
    """Run a reachability probe, retrying only genuine transport failures.

    `attempt` performs one probe and returns ``(ok, detail)``. It should let
    transport errors propagate — classifying them is this function's job — and
    return ``(False, detail)`` itself for an answer it did receive but did not
    like (an HTTP 401, a malformed body).

    Args:
        attempt: Coroutine factory performing a single probe attempt.
        target: URL being probed, for the operator-facing message.
        what: Short noun for the thing being probed (e.g. "Qdrant"), used in
            the retry log line.
        retries: Additional attempts after a retryable transport failure. 0
            (default) means single-shot.
        backoff: Seconds before the first retry; doubles each attempt.
        deadline: Total seconds to spend across all attempts. Retrying stops
            once the next backoff would cross this, bounding the worst case
            regardless of `retries`.

    Returns:
        ``(ok, detail)``. ``detail`` is an operator-facing explanation of the
        failure, and is empty when ``ok`` is True.
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    attempt_n = 0
    while True:
        try:
            return await attempt()
        except RETRYABLE_TRANSPORT_ERRORS as exc:
            reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            wait = backoff * (2**attempt_n)
            elapsed = loop.time() - started
            out_of_budget = deadline is not None and elapsed + wait >= deadline
            if attempt_n >= retries or out_of_budget:
                return False, (
                    f"no response from {target} after {attempt_n + 1} attempt(s) "
                    f"in {elapsed:.1f}s — last error: {reason}. The host is down, "
                    f"still starting, or unreachable from here"
                )
            logger.warning(
                "%s unreachable at %s (attempt %d/%d, %s) — retrying in %.1fs",
                what,
                target,
                attempt_n + 1,
                retries + 1,
                reason,
                wait,
            )
            await asyncio.sleep(wait)
            attempt_n += 1
        except httpx.HTTPError as exc:
            return False, (
                f"request to {target} failed and cannot be retried "
                f"({type(exc).__name__}: {exc}) — check the configured URL"
            )
