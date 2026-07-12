"""ATS client library: one fetch_<ats>() per supported ATS.

Every client normalizes to a list of dicts:
    {"id": str, "title": str, "location": str, "url": str, "posted": str}

All network calls go through `_session_request`, which applies a timeout and
retry-with-backoff on 429/5xx. Callers are responsible for catching
exceptions per-company (see main.py / resolve.py) -- clients here raise on
persistent failure rather than swallowing errors, so callers can log and
skip.
"""
from __future__ import annotations

import random
import time
from typing import Any

import requests

USER_AGENT = "jobwatch/1.0 (+https://github.com/)"
DEFAULT_TIMEOUT = 20
MAX_RETRIES = 3
BACKOFF_BASE = 1.5

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})


class AtsError(Exception):
    """Raised when an ATS endpoint cannot be fetched after retries."""


def request_with_retry(method: str, url: str, **kwargs: Any) -> requests.Response:
    """Public retry-with-backoff request helper, shared with main.py (e.g. Telegram calls)."""
    return _request(method, url, **kwargs)


def _request(method: str, url: str, max_retries: int | None = None, **kwargs: Any) -> requests.Response:
    if max_retries is None:
        max_retries = MAX_RETRIES
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = _session.request(method, url, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt == max_retries:
                raise AtsError(f"{method} {url} failed: {exc}") from exc
            time.sleep(_backoff_delay(attempt))
            continue

        if resp.status_code == 429 or resp.status_code >= 500:
            last_exc = AtsError(f"{method} {url} -> HTTP {resp.status_code}")
            if attempt == max_retries:
                raise last_exc
            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else _backoff_delay(attempt)
            time.sleep(delay)
            continue

        return resp

    raise AtsError(f"{method} {url} failed") from last_exc


def _backoff_delay(attempt: int) -> float:
    return (BACKOFF_BASE**attempt) + random.uniform(0, 0.5)


def _get(url: str, **kwargs: Any) -> requests.Response:
    return _request("GET", url, **kwargs)


def _probe_get(url: str, **kwargs: Any) -> requests.Response:
    """Fail-fast GET for probers: no 429/5xx retries, so speculative slug
    probing can't amplify traffic against an already rate-limited host."""
    return _request("GET", url, max_retries=0, **kwargs)


def _post(url: str, **kwargs: Any) -> requests.Response:
    return _request("POST", url, **kwargs)


# --------------------------------------------------------------------------
# Greenhouse
# --------------------------------------------------------------------------
def fetch_greenhouse(slug: str) -> list[dict]:
    """https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

    Single page, no pagination. Response: {"jobs": [{id, title, location:{name},
    absolute_url, updated_at, first_published, ...}]}
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    resp = _get(url, params={"content": "false"})
    if resp.status_code == 404:
        raise AtsError(f"greenhouse board not found: {slug}")
    resp.raise_for_status()
    data = resp.json()
    out = []
    for job in data.get("jobs", []):
        out.append(
            {
                "id": str(job.get("id")),
                "title": job.get("title", ""),
                "location": (job.get("location") or {}).get("name", ""),
                "url": job.get("absolute_url", ""),
                "posted": job.get("first_published") or job.get("updated_at") or "",
            }
        )
    return out


def probe_greenhouse(slug: str) -> bool:
    # Requires >=1 posting: an empty board can't be distinguished from a
    # name-collision with an unrelated dormant account, and is useless to
    # monitor anyway. Same rule applies to every prober below.
    try:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        resp = _probe_get(url, params={"content": "false"})
        return resp.status_code == 200 and len(resp.json().get("jobs") or []) > 0
    except Exception:
        return False


# --------------------------------------------------------------------------
# Lever
# --------------------------------------------------------------------------
def fetch_lever(slug: str) -> list[dict]:
    """https://api.lever.co/v0/postings/{slug}?mode=json

    Returns a bare JSON array (not wrapped): [{id, text, categories:{location},
    country, hostedUrl, applyUrl, createdAt, ...}]
    Unknown slugs return {"ok": false, "error": "Document not found"} (a dict,
    not a list) -- treated as not-found.
    """
    url = f"https://api.lever.co/v0/postings/{slug}"
    resp = _get(url, params={"mode": "json"})
    if resp.status_code == 404:
        raise AtsError(f"lever board not found: {slug}")
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise AtsError(f"lever board not found: {slug}")
    out = []
    for job in data:
        categories = job.get("categories") or {}
        out.append(
            {
                "id": str(job.get("id")),
                "title": job.get("text", ""),
                "location": categories.get("location", "") or job.get("country", ""),
                "url": job.get("hostedUrl", ""),
                "posted": job.get("createdAt", ""),
            }
        )
    return out


def probe_lever(slug: str) -> bool:
    try:
        url = f"https://api.lever.co/v0/postings/{slug}"
        resp = _probe_get(url, params={"mode": "json"})
        data = resp.json()
        return resp.status_code == 200 and isinstance(data, list) and len(data) > 0
    except Exception:
        return False


# --------------------------------------------------------------------------
# Ashby
# --------------------------------------------------------------------------
def fetch_ashby(slug: str) -> list[dict]:
    """https://api.ashbyhq.com/posting-api/job-board/{slug}

    Public, unauthenticated JSON board API. Response: {"jobs": [{id, title,
    location, jobUrl, applyUrl, publishedAt, isListed, ...}]}
    """
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    resp = _get(url)
    if resp.status_code == 404:
        raise AtsError(f"ashby board not found: {slug}")
    resp.raise_for_status()
    data = resp.json()
    out = []
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue
        out.append(
            {
                "id": str(job.get("id")),
                "title": job.get("title", ""),
                "location": job.get("location", ""),
                "url": job.get("jobUrl") or job.get("applyUrl", ""),
                "posted": job.get("publishedAt", ""),
            }
        )
    return out


def probe_ashby(slug: str) -> bool:
    try:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        resp = _probe_get(url)
        return resp.status_code == 200 and len(resp.json().get("jobs") or []) > 0
    except Exception:
        return False


# --------------------------------------------------------------------------
# Workday (CXS)
# --------------------------------------------------------------------------
def fetch_workday(tenant: str, host: str, site: str) -> list[dict]:
    """POST https://{host}/wday/cxs/{tenant}/{site}/jobs

    `host` is the tenant's full myworkdayjobs.com host, e.g.
    "adobe.wd5.myworkdayjobs.com" (the "wd5" shard varies per tenant).
    Body: {"appliedFacets":{}, "limit":20, "offset":N, "searchText":""}
    Response: {"total": N, "jobPostings": [{title, externalPath, locationsText,
    postedOn, bulletFields}]}. Paginates via offset until offset >= total.
    externalPath is relative -- final apply URL is
    f"https://{host}/{locale}/{site}{externalPath}".
    """
    base = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    limit = 20
    offset = 0
    out: list[dict] = []
    total = None
    while total is None or offset < total:
        resp = _post(
            base,
            json={"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""},
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 404:
            raise AtsError(f"workday site not found: {tenant}/{site}")
        resp.raise_for_status()
        data = resp.json()
        total = data.get("total", 0)
        postings = data.get("jobPostings", [])
        if not postings:
            break
        for job in postings:
            path = job.get("externalPath", "")
            out.append(
                {
                    "id": path or job.get("bulletFields", [""])[0],
                    "title": job.get("title", ""),
                    "location": job.get("locationsText", ""),
                    "url": f"https://{host}/en-US/{site}{path}",
                    "posted": job.get("postedOn", ""),
                }
            )
        offset += limit
        if offset > 5000:  # sanity guard against pathological pagination
            break
    return out


def probe_workday(tenant: str, host: str, site: str) -> bool:
    try:
        base = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        resp = _post(
            base,
            json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
            headers={"Content-Type": "application/json"},
        )
        data = resp.json() if resp.status_code == 200 else {}
        return "jobPostings" in data and data.get("total", 0) > 0
    except Exception:
        return False


# --------------------------------------------------------------------------
# SmartRecruiters
# --------------------------------------------------------------------------
def fetch_smartrecruiters(slug: str) -> list[dict]:
    """https://api.smartrecruiters.com/v1/companies/{slug}/postings

    Response: {"totalFound": N, "offset": 0, "limit": 100, "content": [{id,
    name, location:{city,region,country,fullLocation}, releasedDate, ...}]}.
    Paginates via offset/limit. The public apply page is
    https://jobs.smartrecruiters.com/{slug}/{id} (no extra API call needed).
    """
    base = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    limit = 100
    offset = 0
    out: list[dict] = []
    total = None
    while total is None or offset < total:
        resp = _get(base, params={"limit": limit, "offset": offset})
        if resp.status_code == 404:
            raise AtsError(f"smartrecruiters company not found: {slug}")
        resp.raise_for_status()
        data = resp.json()
        total = data.get("totalFound", 0)
        content = data.get("content", [])
        if not content:
            break
        for job in content:
            loc = job.get("location") or {}
            out.append(
                {
                    "id": str(job.get("id")),
                    "title": job.get("name", ""),
                    "location": loc.get("fullLocation") or loc.get("city", ""),
                    "url": f"https://jobs.smartrecruiters.com/{slug}/{job.get('id')}",
                    "posted": job.get("releasedDate", ""),
                }
            )
        offset += limit
    return out


def probe_smartrecruiters(slug: str) -> bool:
    """SmartRecruiters returns HTTP 200 with totalFound:0 for ANY slug,
    including ones that don't exist -- it never 404s. totalFound > 0 is the
    only reliable signal that `slug` is a real company on the platform.
    A real company with zero currently-open postings will (rarely) be a
    false negative here; that's an acceptable trade-off vs. the alternative
    of matching almost every candidate string to smartrecruiters.
    """
    try:
        url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
        resp = _probe_get(url, params={"limit": 1, "offset": 0})
        return resp.status_code == 200 and resp.json().get("totalFound", 0) > 0
    except Exception:
        return False


# --------------------------------------------------------------------------
# Recruitee
# --------------------------------------------------------------------------
def fetch_recruitee(slug: str) -> list[dict]:
    """https://{slug}.recruitee.com/api/offers/

    Response: {"offers": [{id, title, careers_apply_url, city, country,
    status, created_at, ...}]}. No pagination (returns full list).
    """
    url = f"https://{slug}.recruitee.com/api/offers/"
    resp = _get(url)
    if resp.status_code == 404:
        raise AtsError(f"recruitee company not found: {slug}")
    resp.raise_for_status()
    data = resp.json()
    out = []
    for job in data.get("offers", []):
        if job.get("status") not in (None, "published"):
            continue
        city = job.get("city") or ""
        country = job.get("country") or ""
        location = ", ".join(p for p in (city, country) if p)
        out.append(
            {
                "id": str(job.get("id")),
                "title": job.get("title", ""),
                "location": location,
                "url": job.get("careers_apply_url", ""),
                "posted": job.get("created_at", ""),
            }
        )
    return out


def probe_recruitee(slug: str) -> bool:
    try:
        url = f"https://{slug}.recruitee.com/api/offers/"
        resp = _probe_get(url)
        return resp.status_code == 200 and len(resp.json().get("offers") or []) > 0
    except Exception:
        return False


# --------------------------------------------------------------------------
# Workable
# --------------------------------------------------------------------------
def fetch_workable(slug: str) -> list[dict]:
    """https://apply.workable.com/api/v1/widget/accounts/{slug}

    Public widget JSON, no auth. Response: {"name", "description", "jobs": [
    {title, shortcode, url, application_url, city, state, country,
    published_on, created_at, ...}]}.
    """
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    resp = _get(url)
    if resp.status_code == 404:
        raise AtsError(f"workable account not found: {slug}")
    resp.raise_for_status()
    data = resp.json()
    out = []
    for job in data.get("jobs", []):
        location = ", ".join(p for p in (job.get("city"), job.get("country")) if p)
        out.append(
            {
                "id": job.get("shortcode", ""),
                "title": job.get("title", ""),
                "location": location,
                "url": job.get("url") or job.get("application_url", ""),
                "posted": job.get("published_on") or job.get("created_at", ""),
            }
        )
    return out


def probe_workable(slug: str) -> bool:
    try:
        url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
        resp = _probe_get(url)
        return resp.status_code == 200 and len(resp.json().get("jobs") or []) > 0
    except Exception:
        return False


# --------------------------------------------------------------------------
# BambooHR
# --------------------------------------------------------------------------
def fetch_bamboohr(slug: str) -> list[dict]:
    """https://{slug}.bamboohr.com/careers/list  (public, no auth)

    Response: {"meta": {...}, "result": [{id, jobOpeningName, location:{city,
    state}, isRemote, ...}]}. No pagination, no posted-date field. The public
    posting page is https://{slug}.bamboohr.com/careers/{id}.
    """
    url = f"https://{slug}.bamboohr.com/careers/list"
    resp = _get(url, headers={"Accept": "application/json"})
    if resp.status_code == 404:
        raise AtsError(f"bamboohr account not found: {slug}")
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or "result" not in data:
        raise AtsError(f"bamboohr account not found: {slug}")
    out = []
    for job in data.get("result") or []:
        loc = job.get("location") or {}
        location = ", ".join(p for p in (loc.get("city"), loc.get("state")) if p)
        if job.get("isRemote"):
            location = f"Remote {location}".strip()
        out.append(
            {
                "id": str(job.get("id")),
                "title": job.get("jobOpeningName", ""),
                "location": location,
                "url": f"https://{slug}.bamboohr.com/careers/{job.get('id')}",
                "posted": "",
            }
        )
    return out


def probe_bamboohr(slug: str) -> bool:
    try:
        url = f"https://{slug}.bamboohr.com/careers/list"
        resp = _probe_get(url, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            return False
        data = resp.json()
        return isinstance(data, dict) and len(data.get("result") or []) > 0
    except Exception:
        return False


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workday": fetch_workday,
    "smartrecruiters": fetch_smartrecruiters,
    "recruitee": fetch_recruitee,
    "workable": fetch_workable,
    "bamboohr": fetch_bamboohr,
}

PROBERS = {
    "greenhouse": probe_greenhouse,
    "lever": probe_lever,
    "ashby": probe_ashby,
    "workday": probe_workday,
    "smartrecruiters": probe_smartrecruiters,
    "recruitee": probe_recruitee,
    "workable": probe_workable,
    "bamboohr": probe_bamboohr,
}


def fetch_jobs(entry: dict) -> list[dict]:
    """Dispatch to the right fetcher based on a resolved company config entry."""
    ats = entry["ats"]
    if ats == "workday":
        return fetch_workday(entry["tenant"], entry["host"], entry["site"])
    return FETCHERS[ats](entry["slug"])
