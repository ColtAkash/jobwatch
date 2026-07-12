"""Probe every company in companies.txt against every supported ATS and
write the resolved list into config.json (companies[]), plus
unresolved.txt for anything that didn't match.

Usage: py resolve.py
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import ats

COMPANIES_FILE = Path("companies.txt")
CONFIG_FILE = Path("config.json")
UNRESOLVED_FILE = Path("unresolved.txt")
WORKERS = 15

# ATS types resolve.py can auto-probe purely from a company name (cheap slug
# guessing). Workday/others need an explicit careers_url hint because the
# tenant/site/shard can't be derived from the name alone.
GUESSABLE_ATS = ["greenhouse", "lever", "ashby", "recruitee", "workable", "bamboohr", "smartrecruiters"]

_SUFFIXES = [
    "incorporated", "inc.", "inc", "ltd.", "ltd", "llc", "corp.", "corp",
    "corporation", "limited", "holdings", "group", "financial", "company",
    "companies", "technologies", "communications", "solutions", "canada",
    "international", "worldwide",
]

# Words that are common enough (either as generic English terms or as
# frequent leading words in company names) that trying them alone as a slug
# risks colliding with an unrelated real company on the same ATS platform --
# e.g. "Canada Post" stripped to "post" matched a totally unrelated startup
# called "Post." on Ashby. Never offered as a bare single-word candidate.
_GENERIC_RISK_WORDS = {
    "canadian", "national", "royal", "great", "general", "first", "global",
    "capital", "home", "group", "bank", "life", "post", "care", "work",
    "team", "plus", "prime", "direct", "connect", "express", "central",
    "west", "east", "north", "south", "holdings", "financial", "company",
    "service", "services", "solutions", "systems", "health", "power",
    "energy", "trust", "mutual", "standard", "united", "american", "one",
}

# Stopwords ignored when checking whether an ATS-reported company display
# name plausibly refers to the target company (see names_overlap()).
_NAME_STOPWORDS = {
    "the", "and", "of", "inc", "ltd", "llc", "corp", "corporation",
    "limited", "holdings", "group", "financial", "company", "companies",
    "technologies", "communications", "solutions", "canada", "international",
    "worldwide", "systems", "industries", "enterprises", "co", "gmbh",
}

_WORKDAY_RE = re.compile(r"^([a-z0-9\-]+)\.(wd\d+)\.myworkdayjobs\.com$")

# ATS types that expose a genuine company display name in their public API,
# independent of the URL slug we guessed -- used to reject false-positive
# slug collisions (see verify_identity()). Lever and Ashby don't expose one
# on the postings endpoint, so for those we rely on avoiding generic
# candidates in the first place.
IDENTITY_CHECKABLE_ATS = {"greenhouse", "smartrecruiters", "recruitee", "workable"}


def distinctive_words(name: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", name.lower())
    return {w for w in words if w not in _NAME_STOPWORDS and len(w) >= 3}


def names_overlap(a: str, b: str) -> bool:
    wa, wb = distinctive_words(a), distinctive_words(b)
    if not wa or not wb:
        return True  # can't judge from too-short names -- don't block
    return bool(wa & wb)


def fetch_identity_name(ats_name: str, slug: str) -> str | None:
    """Fetch the ATS's own company display name for `slug`, independent of
    the slug string itself, so it can be cross-checked against the target
    company name. Returns None if unavailable (empty board, network error,
    or an ATS type with no identity field)."""
    try:
        if ats_name == "greenhouse":
            resp = ats.request_with_retry(
                "GET", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "false"}
            )
            jobs = resp.json().get("jobs") or []
            return jobs[0].get("company_name") if jobs else None
        if ats_name == "smartrecruiters":
            resp = ats.request_with_retry(
                "GET", f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params={"limit": 1, "offset": 0}
            )
            content = resp.json().get("content") or []
            return (content[0].get("company") or {}).get("name") if content else None
        if ats_name == "recruitee":
            resp = ats.request_with_retry("GET", f"https://{slug}.recruitee.com/api/offers/")
            offers = resp.json().get("offers") or []
            return offers[0].get("company_name") if offers else None
        if ats_name == "workable":
            resp = ats.request_with_retry("GET", f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
            return resp.json().get("name")
    except Exception:
        return None
    return None


def parse_line(line: str) -> tuple[str, str | None]:
    line = line.strip()
    if not line or line.startswith("#"):
        return "", None
    if "|" in line:
        name, url = line.split("|", 1)
        return name.strip(), url.strip() or None
    return line, None


def slug_candidates(name: str) -> list[tuple[str, bool]]:
    """Ordered most-specific-first (fewer false-positive collisions) to
    least-specific. Returns (candidate, is_weak) pairs: a candidate is
    "weak" when it's a bare single word carved out of a multi-word company
    name (e.g. "lightspeed" from "Lightspeed Commerce"). Weak candidates
    are only safe on ATS platforms whose API exposes a company display name
    we can cross-check (see IDENTITY_CHECKABLE_ATS) -- on Lever/Ashby a
    weak match is unverifiable and gets skipped.

    A bare single leading word is only offered as a last resort, and never
    if it's short or a known generic-collision risk (see
    _GENERIC_RISK_WORDS) -- e.g. "Canada Post" must not offer bare "post"."""
    base = name.lower().strip()
    base = re.sub(r"[^a-z0-9\s\-]", "", base)
    words = base.split()
    stripped_words = [w for w in words if w not in _SUFFIXES] or words

    ordered: list[tuple[str, bool]] = []
    seen: set[str] = set()

    def add(candidate: str, weak: bool = False) -> None:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append((candidate, weak))

    # Most specific: full name (including words like "Canada"/"Group" that
    # may actually be part of the registered slug, e.g. "ubisoft-canada").
    add("-".join(words))
    add("".join(words))
    # Suffix-stripped variants -- only meaningful (and safe) when at least
    # two words remain. When stripping collapses the name to a single word
    # (e.g. "Canada Post" -> "post"), that word goes through the same
    # low-collision-risk gate as the bare-first-word case below, since it's
    # the exact same string.
    if len(stripped_words) >= 2:
        add("-".join(stripped_words))
        add("".join(stripped_words))
    # Bare single word carved from a multi-word name -- last resort, weak.
    first = stripped_words[0] if stripped_words else ""
    if len(words) > 1 and len(first) >= 6 and first not in _GENERIC_RISK_WORDS:
        add(first, weak=True)

    return ordered


def detect_from_url(url: str) -> dict | None:
    """Try to read the ATS + slug straight off a known careers_url."""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
    except ValueError:
        return None
    host = (parsed.netloc or "").lower()
    path = [p for p in (parsed.path or "").split("/") if p]

    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io") and path:
        return {"ats": "greenhouse", "slug": path[0]}
    if host == "jobs.lever.co" and path:
        return {"ats": "lever", "slug": path[0]}
    if host in ("jobs.ashbyhq.com", "www.ashbyhq.com") and path:
        return {"ats": "ashby", "slug": path[0]}
    if host.endswith(".ashbyhq.com"):
        return {"ats": "ashby", "slug": host.split(".")[0]}
    if host == "jobs.smartrecruiters.com" and path:
        return {"ats": "smartrecruiters", "slug": path[0]}
    if host.endswith(".recruitee.com"):
        return {"ats": "recruitee", "slug": host.split(".")[0]}
    if host == "apply.workable.com" and path:
        return {"ats": "workable", "slug": path[0]}
    if host.endswith(".workable.com"):
        return {"ats": "workable", "slug": host.split(".")[0]}
    if host.endswith(".bamboohr.com"):
        return {"ats": "bamboohr", "slug": host.split(".")[0]}

    m = _WORKDAY_RE.match(host)
    if m and path:
        tenant = m.group(1)
        # path is [locale?, site, ...] e.g. /en-US/External, /fr-CA/Site, /External
        if re.fullmatch(r"[a-z]{2}-[a-z]{2}", path[0], re.IGNORECASE) and len(path) > 1:
            site = path[1]
        else:
            site = path[0]
        return {"ats": "workday", "tenant": tenant, "host": host, "site": site}

    return None


def resolve_company(name: str, hint_url: str | None) -> dict | None:
    if hint_url:
        hint = detect_from_url(hint_url)
        if hint:
            # A hint is curated config, not a guess: verify it with the real
            # fetcher (retries + Retry-After handling), accept even an empty
            # board, and never fall through to name-based guessing -- a
            # speculative match would silently override the curated URL.
            entry = {"name": name, **hint}
            try:
                ats.fetch_jobs(entry)
                return entry
            except Exception:
                return None

    for slug, weak in slug_candidates(name):
        for ats_name in GUESSABLE_ATS:
            if weak and ats_name not in IDENTITY_CHECKABLE_ATS:
                continue  # unverifiable single-word match on Lever/Ashby
            try:
                if not ats.PROBERS[ats_name](slug):
                    continue
                if ats_name in IDENTITY_CHECKABLE_ATS:
                    identity = fetch_identity_name(ats_name, slug)
                    if identity is not None and not names_overlap(identity, name):
                        continue  # false-positive slug collision, keep searching
                return {"name": name, "ats": ats_name, "slug": slug}
            except Exception:
                continue
    return None


def main() -> int:
    if not COMPANIES_FILE.exists():
        print(f"missing {COMPANIES_FILE}", file=sys.stderr)
        return 1

    lines = COMPANIES_FILE.read_text(encoding="utf-8").splitlines()
    entries = [parse_line(l) for l in lines]
    entries = [(name, url) for name, url in entries if name]

    resolved: list[dict] = []
    unresolved: list[str] = []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(resolve_company, name, url): name for name, url in entries}
        done = 0
        total = len(futures)
        for future in as_completed(futures):
            name = futures[future]
            done += 1
            try:
                result = future.result()
            except Exception as exc:
                print(f"  [{done}/{total}] ERROR resolving {name}: {exc}", file=sys.stderr)
                result = None
            if result:
                resolved.append(result)
                print(f"  [{done}/{total}] {name} -> {result['ats']}")
            else:
                unresolved.append(name)
                print(f"  [{done}/{total}] {name} -> UNRESOLVED")

    resolved.sort(key=lambda e: e["name"].lower())
    unresolved.sort(key=str.lower)

    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
    config["companies"] = resolved
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    UNRESOLVED_FILE.write_text("\n".join(unresolved) + ("\n" if unresolved else ""), encoding="utf-8")

    total = len(entries)
    print(f"\nResolved {len(resolved)}/{total} companies ({len(unresolved)} unresolved).")
    print(f"Wrote {CONFIG_FILE} and {UNRESOLVED_FILE}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
