import time

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..config import DELAY_SECONDS, TIMEOUT_SECONDS, USER_AGENT


class Fetcher:
    """One client per crawl, with a hard floor on the gap between requests."""

    def __init__(self, delay: float = DELAY_SECONDS):
        self.delay = delay
        self._last_request = 0.0
        self.client = httpx.Client(
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=5, min=5, max=60),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    def get(self, url: str) -> httpx.Response:
        self._throttle()
        resp = self.client.get(url)
        # Back off properly on rate limiting rather than hammering through it.
        if resp.status_code in (429, 503):
            resp.raise_for_status()
        return resp

    def close(self) -> None:
        self.client.close()
