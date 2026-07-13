# jobwatch

Hourly job monitor for new-grad / junior **ML · Data · SWE** roles in Canada.

Every hour, a GitHub Actions job polls the public JSON APIs of **130
companies' applicant-tracking systems** (no scraping, no AI/LLM, no paid
services), filters the postings by title and location, dedupes against
everything already seen, and pushes anything new to Telegram as a digest
grouped by company — with the apply link and a LinkedIn recruiter-search
link for each company.

## Supported ATS platforms (8)

| ATS | Public JSON endpoint |
|---|---|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{slug}/jobs` |
| Lever | `api.lever.co/v0/postings/{slug}?mode=json` |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{slug}` |
| Workday (CXS) | `POST {tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` |
| SmartRecruiters | `api.smartrecruiters.com/v1/companies/{slug}/postings` |
| Recruitee | `{slug}.recruitee.com/api/offers/` |
| Workable | `apply.workable.com/api/v1/widget/accounts/{slug}` |
| BambooHR | `{slug}.bamboohr.com/careers/list` |

All endpoints were verified against live boards. Every client normalizes to
`{id, title, location, url, posted}`.

## How it works

```
companies.txt ──> resolve.py ──> config.json (companies[] + filters)
                                     │
                              main.py (hourly via Actions)
                                     │
              fetch 130 boards in parallel (15 threads, retry w/ backoff)
                                     │
              filter: role_keywords − exclude_title_keywords, Canada locations
                                     │
              dedupe vs seen.json (committed back to the repo — no DB)
                                     │
                       Telegram digest (grouped, ≤4096-char chunks)
```

- **`companies.txt`** — one company per line, optionally `Name | careers_url`.
  A URL hint is treated as curated config: verified with the real fetcher,
  never overridden by name-based slug guessing.
- **`resolve.py`** — probes every company against all 8 ATS platforms and
  writes the matches into `config.json`; leftovers go to `unresolved.txt`.
  Slug guessing is hardened against name collisions (company-identity
  cross-check, boards must have ≥1 posting, ambiguous single-word slugs are
  skipped on ATS types whose API exposes no company name).
- **`main.py`** — the hourly monitor. Per-company failures are isolated (one
  dead endpoint never kills the run), 429/5xx are retried with capped
  backoff, and a company being seen for the first time has its back-catalog
  recorded silently instead of flooding the digest. If every Telegram send
  fails, `seen.json` is not saved, so the digest retries next run.

## Filters (config.json)

- **`role_keywords`** — a title must contain at least one (software/data/ML
  roles, DevOps/SRE/platform, analytics, quant, French "développeur", …).
- **`exclude_title_keywords`** — and none of: seniority markers (senior,
  staff, principal, lead, II/III/IV, manager…), intern/co-op/student,
  non-software engineering disciplines (mechanical, electrical, firmware,
  network…), non-engineering functions (sales, product manager, designer,
  recruiter…).
- **`locations`** — Canadian cities/provinces or "remote Canada"; postings
  with a **blank** location are kept (better a maybe than a silent drop).

Edit the lists and push — the next hourly run picks them up. Note: widening
the filters makes previously-ignored open postings count as "new" once, so
expect a one-time catch-up digest after such a change.

## Setup

```bash
pip install -r requirements-dev.txt   # requests + pytest
python resolve.py                     # rebuild config.json from companies.txt
python main.py                        # first run seeds seen.json silently
```

### Secrets (GitHub Actions)

| Secret | Where it comes from |
|---|---|
| `TELEGRAM_TOKEN` | message [@BotFather](https://t.me/BotFather) → `/newbot` |
| `TELEGRAM_CHAT_ID` | message your bot once, then read `chat.id` from `api.telegram.org/bot<TOKEN>/getUpdates` |

```bash
gh secret set TELEGRAM_TOKEN --app actions
gh secret set TELEGRAM_CHAT_ID --app actions
```

Never commit these. The workflow reads them from repository secrets; local
runs read them from environment variables.

## Testing

```bash
python -m pytest -q     # 30 tests, all HTTP mocked — no network
```

Covers: title/location filtering, dedupe-key stability and URL fallback,
Telegram Markdown escaping and 4096-char chunking on entity-safe
boundaries, per-company failure isolation (10 dead slugs → zero unhandled
exceptions), first-sight seeding, and the send/save ordering.

## Companies that stay manual (~30)

Employers on Phenom, SuccessFactors, Taleo, Njoyn, Oracle, or fully custom
portals expose no public JSON API and are **not scraped** — they remain in
`companies.txt` / `unresolved.txt` as tracked holdouts: CRA, Ontario Public
Service, City of Toronto, TTC, Statistics Canada, Canada Post, Bank of
Canada, Shopify, Scotiabank, TELUS, Rogers, Bell, Air Canada, WestJet,
National Bank, OpenText, Ceridian, Deloitte, EY, KPMG, CGI, Lightspeed,
FreshBooks, Coveo, Clio, Vidyard, Kinaxis, Celestica, Nutrien, Hydro One.
Check these ones by hand.

## Non-goals

No AI/LLM/fit-scoring, no paid scraping services (Apify/SerpAPI), no
LinkedIn/Indeed scraping, no database, no web UI, no Docker. Postings only,
unranked, `requests` as the only runtime dependency.
