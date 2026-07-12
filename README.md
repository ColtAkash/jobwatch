# jobwatch

Hourly job monitor across Canadian companies. Hits each company's ATS JSON
API directly (no scraping, no AI/LLM), filters to new-grad/junior
ML/Data/SWE roles located in Canada, dedupes against previously-seen
postings, and pushes new postings to Telegram as a digest grouped by
company.

## How it works

1. **`companies.txt`** — one company per line, optionally `Name | careers_url`
   when the plain name isn't enough to guess an ATS slug.
2. **`resolve.py`** — probes every company against 8 supported ATS platforms
   (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Recruitee, Workable,
   BambooHR) and writes the resolved list into `config.json`, with anything
   unresolved listed in `unresolved.txt`. A `| careers_url` hint in
   `companies.txt` is treated as curated config: it is verified with the
   real fetcher and never overridden by name-based slug guessing.
3. **`main.py`** — fetches all resolved companies in parallel (15 workers),
   filters by title/location, dedupes against `seen.json`, and sends a
   Telegram digest for anything new. Runs hourly via
   `.github/workflows/jobwatch.yml`, which commits the updated `seen.json`
   back to the repo so state persists between runs (no database).

## Setup

```
pip install -r requirements-dev.txt   # requests + pytest
py resolve.py                          # populates config.json / unresolved.txt
py main.py                             # first run seeds seen.json silently
```

### Secrets (GitHub Actions)

Set `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` as repository secrets. Never
commit them.

## Companies that need manual attention

Anything in `unresolved.txt` after `resolve.py` either:
- uses an ATS not in the supported list and doesn't expose a public JSON
  API (marked `"manual": true` candidates — not scraped), or
- is a true no-API holdout (e.g. government/Crown corporations) and is
  expected to stay manual indefinitely.

## Testing

```
py -m pytest -q
```

All HTTP is mocked in tests — no network calls during `pytest`.

## Non-goals

No AI/LLM/fit-scoring, no paid scraping services, no database, no web UI,
no Docker, no LinkedIn/Indeed scraping. Postings only, unranked.
