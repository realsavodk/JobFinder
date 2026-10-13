# Job Scout — Project Brief

Personalized job board. Pulls postings from company career pages (via public ATS feeds), extracts structured fields, scores each against the user's profile, and shows ranked matches on a dashboard. **The app never applies to jobs.** The user applies manually.

## Status

- **Phase 1 (Skeleton) — done.** Greenhouse, Lever, Ashby, and Workday adapters built and verified against live APIs; fixture tests built from real responses. CLI `job-scout fetch` fills the DB.
- **Phase 2 (MVP) — done.** Keyword-only scoring (`scoring/score.py` `_keyword_score`, `scoring/seniority.py` heuristic), FastAPI + Jinja2 + HTMX dashboard (ranked list, filters, status/notes/follow-up tracking), APScheduler daily job, expiry.
- **Phase 3 (Smarter) — done, with one real gap.** `llm/` provider interface (Ollama default via native `/api/chat` structured outputs, OpenAI-compatible swappable), structured scoring dispatcher (`_structured_score`, falls back to keyword scoring when extraction hasn't succeeded), cross-source dedup (`pipeline/dedup.py`), `eval-extraction` CLI with 20 hand-labeled real fixtures (`evals/`). **Not yet verified against a real Ollama instance** — Ollama isn't installed anywhere reachable from this dev setup, so all LLM provider code is validated only via mocked tests (`tests/llm/`, `tests/pipeline/test_extract.py`). See "What's left" below.
- **Phase 4 (Polish) — not started.** Embeddings in score, email digest, metrics page.

### What's left
1. **Verify Ollama for real.** Install it (locally or on a remote GPU machine, pointed at via `OLLAMA_BASE_URL`) and confirm the `/api/chat` structured-output round-trip actually works — specifically whether its grammar engine handles the `Optional`/nullable fields in `PostingExtraction`'s JSON schema (they compile to `anyOf` unions). If it misbehaves, switch the affected fields to sentinel defaults instead of `None` — not a redesign.
2. **Review the hand-labeled eval fixtures** in `evals/labels.yaml` — labeled by Claude reading the real posting text in one pass, not reviewed ground truth. Correct before trusting `eval-extraction`'s accuracy numbers.
3. **No DB migrations.** Schema changes (several happened across Phases 2–3) still mean deleting the local `job_scout.db` and re-running `fetch` — no migration tool has been added (not in the stack, not asked for yet).
4. **README not written yet** — Conventions below call for one (setup, how to add a company, how to find an ATS token).
5. **Phase 4** is fully unstarted: embedding similarity in scoring, email digest, metrics page (postings/day, score distribution, applied → response rate).

## Goals
- Eliminate the job-*finding* step; nothing else.
- Only use public, official-or-public job feeds. **No scraping of LinkedIn, Indeed, Glassdoor**, or any site whose terms prohibit it.
- Ship a working MVP fast, then iterate. Each phase below should end in something runnable.

## Stack
- Python 3.12, `uv` for deps
- FastAPI (API + server-rendered dashboard with Jinja2 + HTMX; no SPA)
- SQLite via SQLModel (keep it swappable for Postgres later)
- `httpx` (async) for fetching, `APScheduler` for daily polling
- LLM via a small provider interface (`llm/`), provider selected by `LLM_PROVIDER` in `.env`:
  - **Ollama (default)**: local, native `/api/chat` with structured outputs (JSON schema `format`), model from env, not hardcoded
  - **OpenAI-compatible (swappable)**: xAI etc., `base_url` + API key from env, for when you want a hosted model instead
- `pytest` for tests; `ruff` for lint/format

## Config
- `.env`: `LLM_PROVIDER` (`ollama` default, or `openai_compatible`), `LLM_MODEL`, `LLM_NUM_CTX` (default `8192`), `OLLAMA_BASE_URL` (default `http://localhost:11434`), `LLM_API_KEY` + `LLM_BASE_URL` (only used by the `openai_compatible` provider), `DATABASE_URL`
- `companies.yaml`: target companies. Greenhouse/Lever/Ashby: `{name, ats, token}`. Workday: `{name, ats: workday, tenant, host (e.g. wd3), site, searchText (optional, per company)}`
- `profile.yaml`: user profile — majors, skills (with weight), tools, years of experience, seniority targets, locations, remote OK, exclude keywords

Ship `.env.example`, `companies.example.yaml`, `profile.example.yaml`. Never commit real keys.

## Data sources (one adapter per ATS, common interface)
Each adapter returns a list of normalized `RawPosting {source, company, external_id, title, location, url, description_html, posted_at, updated_at}`.

| ATS | Endpoint | Notes |
|---|---|---|
| Greenhouse | `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | Official, documented |
| Lever | `GET https://api.lever.co/v0/postings/{token}?mode=json` | Official, documented |
| Ashby | `GET https://api.ashbyhq.com/posting-api/job-board/{token}` | Official, documented |
| Workday | `POST https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` with `{"limit":20,"offset":0,"searchText":searchText,"appliedFacets":{}}` — `searchText` from the company's `companies.yaml` entry, default `""` | **Undocumented**, may change. Paginate by offset, incrementing by `limit`, until `offset >= total` from the response. Fetch job detail (separate request per job) only for `external_id`s not already in the DB — skip detail fetch for postings already seen. |

Verify every endpoint against live responses before relying on field names; write fixtures from real responses for tests. Rate-limit (≤1 req/sec per host), set a descriptive User-Agent, retry with backoff, and fail one company without failing the run.

## LLM extraction
- `llm/` module: a small provider interface — `extract(posting_text) -> PostingExtraction`. One function, swappable implementation.
- **Ollama provider (default)**: native `POST {OLLAMA_BASE_URL}/api/chat`, `format` = the `PostingExtraction` Pydantic model's JSON schema, `temperature=0`, `options.num_ctx=LLM_NUM_CTX`. Strip HTML from the description and truncate to fit `num_ctx` before sending — Ollama silently truncates on overflow otherwise, so do it ourselves and account for it.
- **OpenAI-compatible provider (swappable)**: same interface, hits `LLM_BASE_URL` with `LLM_API_KEY`, for xAI or similar hosted models.
- Extractions run **sequentially** (local model, one at a time — no concurrent requests to Ollama).
- Timeout + one retry per posting; on repeated failure mark `extraction_failed` and fall back to keyword-only scoring for that posting (never block the run on one bad extraction).
- Cache by content hash (hash of the stripped description) so only new or changed postings get re-extracted.
- On startup, check Ollama is reachable (`GET /api/tags`) and the configured model is in the list; if not, log a clear message and skip extraction for the run rather than crash — keyword-only scoring still runs.

## Pipeline
1. **Fetch** all companies → `RawPosting`s
2. **Upsert** keyed on `(source, company, external_id)`; track `first_seen`, `last_seen`
3. **Expire** postings not seen in the latest successful run for that company → `status=closed`
4. **Dedup** across sources: same company + normalized title + location → link as duplicates, show once
5. **Extract** (LLM, only for new/changed postings, cache by content hash — see LLM extraction above): JSON with `seniority`, `min_years`, `required_skills[]`, `nice_skills[]`, `degree_fields[]`, `location`, `remote` (`onsite|hybrid|remote`), `role_family`. Validate with Pydantic; on failure, retry once, then mark `extraction_failed` and fall back to keyword-only scoring.
6. **Score** 0–100 against `profile.yaml`:
   - Hard filters: location/remote, seniority, `min_years` ≤ profile + 1, exclude keywords
   - Weighted overlap: required skills (high weight), nice skills (low), degree field match
   - Phase 4: blend in embedding similarity (profile text vs. posting)
   - Store a short `score_reasons` list so the dashboard can explain each match
7. **Notify** (phase 4): email digest of new postings above a threshold

## Dashboard
- Ranked list: title, company, location, score, reasons, posted date, "Open posting" link (opens the original page; the user applies there)
- Filters: min score, company, remote, new since last visit
- Per-posting status set by user: `new / interested / applied / rejected / hidden`, plus notes and follow-up date
- Page to edit profile and company list (or just read the YAML files in MVP)
- "Run now" button + last-run status per company

## Phases
1. **Skeleton:** project setup, models, Greenhouse + Lever + Ashby + Workday adapters with fixture tests (fixtures built from real responses), CLI `fetch` command that fills the DB
2. **MVP:** keyword-only scoring from `profile.yaml`, dashboard list with filters + status tracking, daily scheduler, expiry
3. **Smarter:** LLM extraction + structured scoring, cross-source dedup
4. **Polish:** embeddings in score, email digest, simple metrics page (postings/day, score distribution, applied → response rate)

## Conventions
- Small modules: `adapters/`, `pipeline/`, `scoring/`, `llm/`, `web/`, `models.py`, `config.py`, `cli.py`
- Type hints everywhere; Pydantic for all external data
- Tests for every adapter (fixtures, no live network in tests) and for scoring; LLM provider tests mock the provider — no live model calls in the test suite
- `eval-extraction` CLI command: runs the configured LLM provider over a fixtures/ set of real postings with hand-labeled expected fields (YAML), reports per-field accuracy — lets you compare models/prompts without guessing
- README with setup, how to add a company, how to find a company's ATS token (look at its careers page URL / network requests)
- Ask before adding heavy dependencies or changing the stack

## Out of scope
- Auto-applying, form filling, or account automation of any kind
- Scraping job boards / aggregators
- Multi-user auth (single user for now)
