"""Freshdesk API client: rate-limit-aware, paginating, date-windowing, streaming.

One client instance per account. The SOURCE client is used read-only by
construction (we only ever call read helpers on it); the TARGET client is the
only one that issues writes.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class _DryResponse:
    """Stand-in for a requests.Response used in --dry-run: every write returns a
    synthetic success with a unique NEGATIVE fake id, so the whole pipeline runs
    end-to-end (downstream phases get an id to reference) without any real write."""
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)
        self.headers: Dict[str, str] = {}
        self.content = self.text.encode()

    def json(self) -> Any:
        return self._payload

PAGE_SIZE = 100              # Freshdesk max per_page
LIST_PAGE_CAP = 300         # Freshdesk hard cap: 300 pages (30,000 records) per filtered list
DEFAULT_TIMEOUT = 60
ATTACHMENT_TIMEOUT = 600    # large window for call recordings / big files


class FreshdeskApiError(Exception):
    def __init__(self, status_code: int, body: str, method: str = "", path: str = ""):
        self.status_code = status_code
        self.body = body
        super().__init__(f"{method} {path} -> HTTP {status_code}: {body[:300]}")


class FreshdeskClient:
    def __init__(self, base_url: str, api_key: str, logger: logging.Logger,
                 label: str = "", rate_limit_floor: int = 3, dry_run: bool = False):
        self.base_url = base_url.rstrip("/")
        self.api = f"{self.base_url}/api/v2"
        self.label = label
        self.logger = logger
        self.rate_limit_floor = rate_limit_floor
        # dry_run: reads (GET) stay real; writes (POST/PUT/DELETE) are intercepted
        # and logged, never sent. Only ever set on the TARGET client.
        self.dry_run = dry_run
        self._dry_seq = 0

        self.session = requests.Session()
        self.session.auth = (api_key, "X")  # Freshdesk uses api_key as username, any password
        retry = Retry(
            total=4, backoff_factor=2.0,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    # ------------------------------------------------------------------ core
    def _throttle(self, resp: requests.Response) -> None:
        """Drive pacing off the live rate-limit headers Freshdesk returns."""
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            try:
                if int(remaining) <= self.rate_limit_floor:
                    self.logger.info(f"[{self.label}] rate-limit floor reached "
                                     f"({remaining} left) - pausing 5s")
                    time.sleep(5)
            except ValueError:
                pass

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = path if path.startswith("http") else f"{self.api}{path}"
        timeout = kwargs.pop("timeout", DEFAULT_TIMEOUT)
        for attempt in range(8):
            resp = self.session.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "30")) + 1
                self.logger.warning(f"[{self.label}] 429 on {method} {path} - sleeping {wait}s "
                                    f"(attempt {attempt + 1}/8)")
                time.sleep(wait)
                continue
            if 500 <= resp.status_code < 600 and attempt < 7:
                wait = 2 ** attempt
                self.logger.warning(f"[{self.label}] {resp.status_code} on {method} {path} - "
                                    f"retry in {wait}s")
                time.sleep(wait)
                continue
            self._throttle(resp)
            return resp
        return resp  # type: ignore[return-value]

    def _json(self, resp: requests.Response, method: str, path: str) -> Any:
        if resp.status_code >= 400:
            raise FreshdeskApiError(resp.status_code, resp.text, method, path)
        if not resp.content:
            return None
        return resp.json()

    # ----------------------------------------------------------------- verbs
    def get(self, path: str, **kwargs) -> Any:
        r = self._request("GET", path, **kwargs)
        return self._json(r, "GET", path)

    def get_raw(self, path: str, **kwargs) -> requests.Response:
        return self._request("GET", path, **kwargs)

    def _dry(self, method: str, path: str) -> _DryResponse:
        self._dry_seq += 1
        self.logger.info(f"[DRY-RUN] {method} {path} (not sent)")
        return _DryResponse(201 if method == "POST" else 200, {"id": -self._dry_seq})

    def post(self, path: str, **kwargs) -> requests.Response:
        if self.dry_run:
            return self._dry("POST", path)  # type: ignore[return-value]
        return self._request("POST", path, **kwargs)

    def put(self, path: str, **kwargs) -> requests.Response:
        if self.dry_run:
            return self._dry("PUT", path)  # type: ignore[return-value]
        return self._request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs) -> requests.Response:
        if self.dry_run:
            return self._dry("DELETE", path)  # type: ignore[return-value]
        return self._request("DELETE", path, **kwargs)

    # ------------------------------------------------------------- pagination
    def paginate(self, path: str, params: Optional[Dict] = None) -> Iterator[Dict]:
        """Generic paginated list. Stops at the 300-page cap (use iter_tickets
        for ticket volumes that may exceed 30,000)."""
        page = 1
        params = dict(params or {})
        params.setdefault("per_page", PAGE_SIZE)
        while page <= LIST_PAGE_CAP:
            params["page"] = page
            resp = self.get_raw(path, params=params)
            if resp.status_code == 404:
                return
            if resp.status_code >= 400:
                raise FreshdeskApiError(resp.status_code, resp.text, "GET", path)
            items = resp.json()
            if isinstance(items, dict):  # some endpoints wrap (e.g. {"results": [...]})
                items = next((v for v in items.values() if isinstance(v, list)), [])
            if not items:
                return
            for item in items:
                yield item
            if len(items) < params["per_page"]:
                return
            page += 1
        # Fell out of the loop: the endpoint had MORE than 300 pages of results.
        self.logger.warning(f"[{self.label}] GET {path} hit the 30,000-record list cap - "
                            "results are TRUNCATED (use a windowed iterator for this volume)")

    def iter_windowed(self, path: str, updated_since: str,
                      extra_params: Optional[Dict] = None) -> Iterator[Dict]:
        """Iterate ALL records of a list endpoint that supports
        `updated_since` + `order_by=updated_at` ascending (tickets and contacts
        do; companies do not), transparently date-windowing past Freshdesk's
        30,000-record (300-page) list cap.

        Strategy: page ordered by updated_at ascending within a moving
        `updated_since` window. When a window is exhausted (< full page
        returned) we're done; if we hit the 300-page cap first, we advance the
        window to the last seen updated_at and continue. A `seen` set dedupes
        the unavoidable boundary overlap.
        """
        seen: set[int] = set()
        window = updated_since
        while True:
            page = 1
            last_updated: Optional[str] = None
            new_in_window = 0
            exhausted = False
            while page <= LIST_PAGE_CAP:
                params = {
                    "per_page": PAGE_SIZE,
                    "page": page,
                    "order_by": "updated_at",
                    "order_type": "asc",
                    "updated_since": window,
                }
                if extra_params:
                    params.update(extra_params)
                resp = self.get_raw(path, params=params)
                if resp.status_code == 404:
                    return
                if resp.status_code >= 400:
                    raise FreshdeskApiError(resp.status_code, resp.text, "GET", path)
                items = resp.json() or []
                if not items:
                    exhausted = True
                    break
                for t in items:
                    last_updated = t.get("updated_at") or last_updated
                    if t["id"] not in seen:
                        seen.add(t["id"])
                        new_in_window += 1
                        yield t
                if len(items) < PAGE_SIZE:
                    exhausted = True
                    break
                page += 1
            if exhausted or last_updated is None or new_in_window == 0:
                return
            if window == last_updated:
                # Pathological: >30k records share one timestamp. Can't advance
                # safely without risking an infinite loop - stop and warn.
                self.logger.warning(f"{path} window could not advance past "
                                    f"{window}; stopping to avoid a loop.")
                return
            self.logger.info(f"Advancing {path} window past 30k cap -> updated_since={last_updated}")
            window = last_updated

    def iter_tickets(self, updated_since: str, include_spam: bool = False) -> Iterator[Dict]:
        """All tickets, windowed past the 30k cap. NOTE: the LIST payload has no
        description/attachments/requester - fetch the ticket DETAIL before
        building a create payload."""
        extra = {"filter": "spam"} if include_spam else None
        return self.iter_windowed("/tickets", updated_since, extra)

    def iter_contacts(self, updated_since: str) -> Iterator[Dict]:
        """All contacts, windowed past the 30k cap (the /contacts endpoint
        supports updated_since + order_by=updated_at like /tickets)."""
        return self.iter_windowed("/contacts", updated_since)

    # ------------------------------------------------------------ attachments
    def download_to(self, url: str, dest: Path) -> bool:
        """Stream a pre-signed Freshdesk attachment URL to disk (NO auth header -
        the URLs are pre-signed S3). Streaming keeps large call recordings off
        the heap."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with requests.get(url, stream=True, timeout=ATTACHMENT_TIMEOUT) as r:
                if r.status_code != 200:
                    self.logger.warning(f"Attachment download {r.status_code}: {url[:80]}")
                    return False
                with open(dest, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        if chunk:
                            fh.write(chunk)
            return True
        except requests.RequestException as e:
            self.logger.warning(f"Attachment download error: {e}")
            return False

    # -------------------------------------------------------------- identity
    def whoami(self) -> Dict:
        """Verify access. Raises FreshdeskApiError on bad domain/key."""
        return self.get("/agents/me")
