import logging
import time
from dataclasses import dataclass, field

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..config import DELAY_SECONDS, TIMEOUT_SECONDS, USER_AGENT

log = logging.getLogger(__name__)

# Answers worth a second try: rate limiting and a proxy in front of the portal
# timing out (Casa Sapo answered one 504 mid-crawl on 2026-11-16).
RETRY_STATUS = (429, 500, 502, 503, 504)
# Share of search pages that must answer for a run with some failed pages to
# count as "incomplete" (job green, lifecycle runs) rather than "failed".
INCOMPLETE_MIN = 0.9


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
    def get(self, url: str, **kw) -> httpx.Response:
        self._throttle()
        resp = self.client.get(url, **kw)
        # Back off properly on rate limiting and gateway blips rather than
        # hammering through them; each retry still waits the full delay.
        if resp.status_code in RETRY_STATUS:
            resp.raise_for_status()
        return resp

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=5, min=5, max=60),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    def post(self, url: str, **kw) -> httpx.Response:
        self._throttle()
        resp = self.client.post(url, **kw)
        if resp.status_code in RETRY_STATUS:
            resp.raise_for_status()
        return resp

    def close(self) -> None:
        self.client.close()


@dataclass
class CrawlContext:
    """What a source's crawl() gets: a throttled client, the database, and
    where to look. save() stores one page in raw_pages and commits, so a crash
    halfway keeps everything fetched so far."""
    fetcher: Fetcher
    conn: object
    run_id: int
    source: str
    councils: list[str]
    detail_limit: int
    pages: int = 0
    items: int = 0
    problems: list[str] = field(default_factory=list)
    # Search pages that answered and pages that still failed after every
    # retry. A few failed pages make the run "incomplete", not "failed".
    pages_ok: int = 0
    failed_pages: list[str] = field(default_factory=list)

    def save(self, kind: str, url: str, status: int, payload) -> int:
        from ..db import save_raw_page
        self.pages += 1
        return save_raw_page(self.conn, self.run_id, self.source, kind, url, status, payload)

    def page_failed(self, text: str) -> None:
        """One page of many that could not be read, even on a second pass."""
        log.warning("%s: %s", self.source, text)
        self.failed_pages.append(text)

    def status(self) -> tuple[str, str | None]:
        """('ok' | 'incomplete' | 'failed', error text) for crawl_runs.
        Incomplete: at least INCOMPLETE_MIN of the pages answered. lifecycle.py
        still uses such a run, with a longer grace for that portal."""
        if self.problems:
            return "failed", "; ".join(self.problems + self.failed_pages)[:1000]
        if self.failed_pages:
            share = self.pages_ok / max(self.pages_ok + len(self.failed_pages), 1)
            state = "incomplete" if share >= INCOMPLETE_MIN else "failed"
            return state, f"{len(self.failed_pages)} pages failed: " + "; ".join(self.failed_pages)[:900]
        return "ok", None

    def problem(self, text: str) -> None:
        """Something that makes tonight's crawl incomplete. The run is then
        recorded as failed, so apscrap/lifecycle.py does not read a missing
        advert as gone."""
        log.warning("%s: %s", self.source, text)
        self.problems.append(text)


class Source:
    """A portal other than Imovirtual. Each one decides how to walk its own
    site in crawl(), and turns stored payloads into rows in parse_page().

    parse_page(kind, payload) yields (listing, snapshot) pairs. listing holds
    the listings columns it knows (source, external_id and url at least) plus
    optional extras: latitude, longitude, description, extras, image_urls,
    energy_cert, build_year, deposit_eur, source_updated_at and status
    ('available', 'reserved' or 'rented'). snapshot holds price_eur,
    price_per_sqm and title, or is None when the page only proves the advert
    is still online (a sitemap), in which case the last known price carries
    over to today."""
    name = ""
    # Seconds between requests to this portal.
    delay = DELAY_SECONDS
    # Transactions this source crawls. Rentals only: sales stay on Imovirtual.
    transactions = ("rent",)

    def crawl(self, ctx: CrawlContext) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def parse_page(self, kind: str, payload: dict):  # pragma: no cover - interface
        raise NotImplementedError
