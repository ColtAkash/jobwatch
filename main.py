"""Hourly job monitor: fetch -> filter -> dedupe -> Telegram digest.

Usage: py main.py
Env vars: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID (required to actually send;
missing them is only an error if there is something new to send).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import ats

CONFIG_FILE = Path("config.json")
SEEN_FILE = Path("seen.json")
WORKERS = 15
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MSG_LEN = 4096


def load_config() -> dict:
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def load_seen() -> set[str]:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen(keys: set[str]) -> None:
    SEEN_FILE.write_text(json.dumps(sorted(keys), indent=2), encoding="utf-8")


def is_relevant_title(title: str | None, config: dict) -> bool:
    t = (title or "").lower()
    if any(k in t for k in config["exclude_title_keywords"]):
        return False
    return any(k in t for k in config["role_keywords"])


def is_relevant_location(location: str | None, config: dict) -> bool:
    loc = (location or "").strip().lower()
    if not loc:
        return True
    return any(k in loc for k in config["locations"])


def dedupe_key(company_name: str, job: dict) -> str:
    raw = f"{company_name}:{job.get('id') or job.get('url')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fetch_company_jobs(entry: dict) -> tuple[str, list[dict], str | None]:
    """Never raises -- one dead company must never kill the run."""
    try:
        return entry["name"], ats.fetch_jobs(entry), None
    except Exception as exc:  # noqa: BLE001 - intentionally broad, isolates one company
        return entry["name"], [], str(exc)


def collect_new_postings(
    config: dict, seen: set[str]
) -> tuple[dict[str, list[dict]], set[str], list[tuple[str, str]]]:
    companies = config.get("companies", [])
    new_by_company: dict[str, list[dict]] = {}
    all_keys = set(seen)
    errors: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(fetch_company_jobs, entry) for entry in companies]
        for future in as_completed(futures):
            name, jobs, error = future.result()
            if error:
                errors.append((name, error))
                print(f"  ! {name}: {error}", file=sys.stderr)
                continue
            for job in jobs:
                if not is_relevant_title(job.get("title"), config):
                    continue
                if not is_relevant_location(job.get("location"), config):
                    continue
                key = dedupe_key(name, job)
                all_keys.add(key)
                if key not in seen:
                    new_by_company.setdefault(name, []).append(job)

    return new_by_company, all_keys, errors


def linkedin_recruiter_link(company_name: str) -> str:
    q = quote(f"{company_name} recruiter")
    return f"https://www.linkedin.com/search/results/people/?keywords={q}"


def build_company_block(company: str, jobs: list[dict]) -> str:
    lines = [f"*{company}*"]
    for job in jobs:
        title = job.get("title") or "Untitled"
        url = job.get("url") or ""
        loc = job.get("location") or "n/a"
        lines.append(f"• [{title}]({url}) — {loc}")
    lines.append(f"[Find a recruiter]({linkedin_recruiter_link(company)})")
    return "\n".join(lines)


def build_messages(new_by_company: dict[str, list[dict]]) -> list[str]:
    """Chunk company blocks into Telegram messages, never exceeding
    MAX_MSG_LEN and never splitting a company block across messages (unless
    a single block itself exceeds the limit, in which case it's hard-split).
    """
    blocks = [build_company_block(c, jobs) for c, jobs in sorted(new_by_company.items())]

    messages: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= MAX_MSG_LEN:
            current = candidate
            continue
        if current:
            messages.append(current)
            current = ""
        if len(block) <= MAX_MSG_LEN:
            current = block
        else:
            for i in range(0, len(block), MAX_MSG_LEN):
                messages.append(block[i : i + MAX_MSG_LEN])
    if current:
        messages.append(current)
    return messages


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = TELEGRAM_API.format(token=token)
    resp = ats.request_with_retry(
        "POST",
        url,
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        },
    )
    resp.raise_for_status()


def main() -> int:
    if not CONFIG_FILE.exists():
        print(f"missing {CONFIG_FILE}; run resolve.py first", file=sys.stderr)
        return 1

    config = load_config()
    seen = load_seen()
    first_run = not SEEN_FILE.exists()

    new_by_company, all_keys, errors = collect_new_postings(config, seen)

    if errors:
        print(f"{len(errors)} companies failed this run (continuing): "
              f"{', '.join(name for name, _ in errors)}", file=sys.stderr)

    if first_run:
        save_seen(all_keys)
        print(f"Seeded seen.json with {len(all_keys)} existing postings. No notifications sent.")
        return 0

    if not new_by_company:
        save_seen(all_keys)
        print("No new postings.")
        return 0

    total_new = sum(len(jobs) for jobs in new_by_company.values())
    print(f"{total_new} new posting(s) across {len(new_by_company)} companies.")

    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_TOKEN/TELEGRAM_CHAT_ID not set; skipping send.", file=sys.stderr)
        return 1

    for message in build_messages(new_by_company):
        send_telegram(token, chat_id, message)

    save_seen(all_keys)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
