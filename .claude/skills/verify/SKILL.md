---
name: verify
description: Build/launch/drive recipe for verifying jobwatch changes end-to-end.
---

# Verifying jobwatch

CLI surface, Python 3.12+, `requests` only. On this machine use `py` (the
`python` alias is a Store stub).

## Setup

```
pip install -r requirements-dev.txt
```

## Drive the real flows

- **Resolver:** `py resolve.py` — probes all companies in companies.txt
  against live ATS APIs (~1-2 min, 15 threads). Prints per-company results
  and a final `Resolved N/160` line; writes config.json + unresolved.txt.
  For a fast probe, point it at a 2-3 line companies.txt in a temp dir with
  `PYTHONPATH=<repo> py <repo>/resolve.py`.
- **Monitor, seed run:** delete seen.json, `py main.py` — fetches all ~130
  resolved companies live (~2-3 min), seeds seen.json silently, exit 0.
- **Monitor, delta run:** with seen.json present, `py main.py` — normally
  prints "No new postings." To force exactly one "new" job, pop one key
  from the seen.json array and re-run.
- **Telegram failure path:** `TELEGRAM_TOKEN=123456:FAKE TELEGRAM_CHAT_ID=1
  py main.py` with one key popped — expect a real 401 from api.telegram.org,
  "all telegram sends failed; not saving seen.json", exit 1, and seen.json
  unchanged (restore the key afterwards if you popped one).
- A real send requires genuine secrets — never commit them; treat send-path
  verification with the fake-token probe above instead.

## Gotchas

- Workable rate-limits aggressively (429s for ~an hour) after heavy probing;
  a company on Workable (e.g. Nuvei) can transiently resolve as UNRESOLVED.
  Re-run later rather than chasing it.
- resolve.py rewrites config.json's companies[] but preserves the filter
  keys; don't hand-edit companies[] — fix companies.txt hints instead.
- Tests are mocked-HTTP only: `py -m pytest -q` (CI's job, not verification).
