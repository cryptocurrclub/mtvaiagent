"""Small retry-with-exponential-backoff helper shared by blockchain.py and price_api.py."""
from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


def call_with_retry(
    func: Callable[[], T],
    *,
    max_retries: int,
    backoff_base: float,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    what: str = "operation",
) -> T:
    """Call ``func`` (no-arg callable), retrying on ``retry_on`` exceptions.

    Uses exponential backoff: backoff_base * 2**attempt seconds between
    attempts. Raises the last exception if all attempts fail.
    """
    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except retry_on as exc:
            last_exc = exc
            if attempt == max_retries:
                logger.error("%s failed after %d attempts: %s", what, attempt + 1, exc)
                raise
            wait = backoff_base * (2**attempt)
            logger.warning(
                "%s failed (attempt %d/%d): %s - retrying in %.1fs",
                what,
                attempt + 1,
                max_retries + 1,
                exc,
                wait,
            )
            time.sleep(wait)
    # Unreachable, but keeps type checkers happy.
    assert last_exc is not None
    raise last_exc
