# Implementation Progress

Status values: `NOT_STARTED` · `IN_PROGRESS` · `BLOCKED` · `COMPLETED`.
A phase is `COMPLETED` only when its acceptance criteria are met and the evidence below supports it.

**Repository inspection (2026-10-12):** the repository was empty — no source code, tests, or
configuration existed. No phase has verifiable prior work.

**Last completed phase:** Phase 1 — Mock CMS (2026-10-12).
**Next phase to implement:** Phase 2 — Schema normalization (awaiting user authorization).

| # | Phase | Status |
|---|---|---|
| 1 | Mock CMS | COMPLETED |
| 2 | Schema normalization | NOT_STARTED |
| 3 | LLM mapping proposals | NOT_STARTED |
| 4 | Playwright execution | NOT_STARTED |
| 5 | Durable orchestration (Temporal) | NOT_STARTED |
| 6 | Evaluation + dashboard | NOT_STARTED |
| 7 | Hardening + demonstration | NOT_STARTED |

---

## Phase 1 — Mock CMS
**Status:** COMPLETED (2026-10-12)
**Objective:** A local legal case-management application with login, configurable fields, role-based
permissions and persisted configuration, acting as the target system with real, verifiable state.
**Depends on:** nothing.

Tasks / deliverables
- [x] Repo scaffolding: `.gitignore`, `.env.example`, `docker-compose.yml` (Postgres :5434 + mock-cms
      :8100), `docker/postgres/init/01-create-databases.sql`, `mock-cms/pyproject.toml`
- [x] Data model + Alembic migration `0001` (tenants, users, auth_sessions, field_definitions,
      intake_mappings, intake_records, `tenants.config_revision`); composite FKs enforce same-tenant
      references at the DB level
- [x] Authentication: scrypt password hashes, SHA-256-hashed bearer tokens / browser sessions with
      expiry, HttpOnly SameSite=Lax cookie, per-session CSRF token on every form post
- [x] RBAC: admin, integration, intake_coordinator, staff (`app/permissions.py`); tenant derived from
      the authenticated user only
- [x] JSON API `/api/v1`: token/logout/me, schema discovery, config snapshot, field update, custom
      fields, intake mapping upsert/delete, intake submission, records — structured error envelope
- [x] HTML UI (Jinja2) with label/role-based markup: login, fields, field edit, custom field, mappings,
      intake form, records
- [x] Seed script (`python -m app.seed`) for fictional firms A, B, C × 4 roles; idempotent
- [x] Tests: 75 (unit rules, auth, config API, intake API, RBAC matrix, tenant isolation incl. DB-level
      constraint, UI forms + CSRF, seed)
- [x] Mock CMS container in Compose (non-root, migrates on start)

Acceptance criteria — all met
- [x] A user can log in and manually configure fields and mappings (UI and API).
- [x] Saved configuration is persisted and verifiable through the API **and** a direct DB query.
- [x] Unauthorized roles and other tenants are rejected.
- [x] Form submission enforces required fields/types and returns structured validation errors.

Validation evidence (2026-10-12)
- `pytest`: **75 passed** against real PostgreSQL 17 (`mock_cms_test`), incl. Alembic downgrade→upgrade
  per session. One warning: Starlette deprecation notice about `httpx` in `TestClient` (third-party).
- Mutation check: granting `staff` `fields:write` and disabling CSRF validation produced 4 failing
  tests; restoring the code returned 75 passed — the RBAC/CSRF tests detect regressions.
- `ruff check` + `ruff format --check`: clean. `mypy --strict app tests`: no issues (27 files).
- `pip install -e ".[dev]"`: succeeds. `docker compose up -d --build mock-cms`: image builds, migrates,
  serves `/healthz` and `/login` (200).
- Manual walkthrough against the running container with seeded users: `c.admin` edited
  `contact.email` (required) and created `matter.custom.review_status` and a mapping through HTML forms
  (revisions 1–3); `c.integration` added 3 mappings via API (revisions 4–6); identical repeat PUT
  returned `changed: false`; `c.integration` submitting an intake → 403; `GET /api/v1/config` and a
  `psql` query on `mock_cms` showed identical mappings/fields; firms A and B stayed at revision 0;
  invalid submission → 422 with `required_field_missing` details. Dev DB was then reset and reseeded.

Known limitations (accepted for Phase 1)
- Controlled UI variants (renamed elements for drift tests) deferred to Phase 4, where Playwright needs them.
- No login rate limiting / account lockout (local synthetic system).
- `POST /intake` (HTML) is an async handler doing synchronous DB work; acceptable at mock-CMS load.
- Built-in field keys/types are fixed; only label/description/required/enabled/options are editable.
  Custom fields cannot be deleted (not required by the blueprint so far).

## Phase 2 — Schema normalization
**Status:** NOT_STARTED — **next phase**
**Objective:** Pydantic models for source fields, target fields, canonical entities, mappings and
transformations; normalize multiple firms' schemas into one representation.
**Depends on:** Phase 1 (target schema from mock CMS) — satisfied. Target schema is available at
`GET /api/v1/schema` using the `<firm>.integration` account.

Tasks / deliverables
- [ ] Backend scaffold (`backend/`, FastAPI app, DB models + Alembic for firms, cms_connectors,
      onboarding_projects, schema_definitions, field_definitions)
- [ ] Canonical model (Client, Matter, Intake, Task, Document reference) + extension structure
- [ ] Source schema parsers (JSON schema, field-definition export, sample form)
- [ ] Target schema normalization via mock CMS adapter (`integrations/base.py`, `mock_cms.py`/`api_cms.py`)
- [ ] Fixtures: Firm A/B/C source schemas, target schema snapshot, expected mappings (ground truth)
- [ ] Project + source-schema + inspect-target endpoints

Acceptance criteria: Firms A, B, C normalize consistently; versioned schema definitions stored;
vendor-specific fields preserved in extensions.
Required evidence: unit tests per parser; integration test normalizing all three firms + live mock CMS.

## Phase 3 — LLM mapping proposals
**Status:** NOT_STARTED
**Objective:** LLM proposes mappings via structured tool calls; deterministic validator accepts,
rejects or marks ambiguous. Approval and execution stay separate.
**Depends on:** Phase 2.

Tasks / deliverables
- [ ] Provider-neutral LLM client (Gemini primary; Groq per decision D1), prompts, typed tool registry
- [ ] `mapping_proposals`, `mapping_versions`, `audit_events` tables
- [ ] Deterministic `mapping_validator` (field existence, type compatibility, transformation support,
      required coverage, authorized config area)
- [ ] Propose/list/approve mapping endpoints; approval bound to an immutable mapping version
- [ ] Prompt-injection handling (untrusted text as data)

Acceptance criteria: proposals validated deterministically; approval references a version;
no execution path exists from proposal without approval.
Required evidence: unit tests with a mocked LLM; validator tests for each rejection class; one recorded
live LLM run (if an API key is available, else BLOCKED for that item).

## Phase 4 — Playwright execution
**Status:** NOT_STARTED
**Objective:** Execute an approved configuration in the mock CMS (API and/or UI) and read it back.
**Depends on:** Phases 1–3.

Tasks / deliverables
- [ ] `browser_cms.py` adapter with role/label locators, domain allow-list, traces on failure
- [ ] Execution service: typed, permission-checked, idempotency keys; `onboarding_runs`, `action_steps`
- [ ] Read-after-write verification service; `verification_results`
- [ ] Mock CMS UI-variant switch for controlled UI changes (needed for drift tests)
- [ ] Execute + verification endpoints

Acceptance criteria: approved plan applied and verified by read-back; missing/renamed element stops safely.
Required evidence: browser tests vs running mock CMS; verification report for Firm A.

## Phase 5 — Durable orchestration
**Status:** NOT_STARTED
**Objective:** Temporal workflows for onboarding runs, approvals, retries and recovery.
**Depends on:** Phase 4.

Tasks / deliverables: Temporal in Compose; workflow + activities; approval signal; bounded retries;
uncertain-outcome read-before-retry.
Acceptance criteria: interrupted work resumes without repeating completed side effects.
Required evidence: test killing a worker mid-run and resuming; duplicate-execution test.

## Phase 6 — Evaluation + dashboard
**Status:** NOT_STARTED
**Objective:** Scenario runner, metrics, mapping review UI, audit history.
**Depends on:** Phases 1–5.

Tasks / deliverables: 30–50 labelled scenarios; `scripts/run_evaluations.py`; Next.js dashboard
(projects, schema views, mapping approval, status/logs, report); audit-events endpoint.
Acceptance criteria: one command runs the suite; dashboard explains successes and failures.
Required evidence: evaluation report with actual metrics.

## Phase 7 — Hardening + demonstration
**Status:** NOT_STARTED
**Objective:** Test unauthorized actions, UI changes, timeouts, duplicates, schema changes, untrusted input;
produce README and demo.
**Depends on:** Phase 6.

Acceptance criteria: repeatable demo of normal onboarding + at least one deliberate failure with safe
recovery; README with architecture, install, firms, dataset, results, failure scenarios.
Required evidence: demo script/recording; CI green.

---

## Decisions (resolved by the user, 2026-10-12)
- **D1 LLM provider — RESOLVED:** Gemini primary via the Google Python SDK (`google-genai`), Groq
  optional fallback, provider-neutral module `backend/app/agent/llm_client.py`.
- **D2 Blueprint in repo — RESOLVED:** committed as [`docs/BLUEPRINT.md`](docs/BLUEPRINT.md).
- **D3 Version control — RESOLVED:** git repo, remote
  `https://github.com/kshitij189/Legal-CMS-Onboarding-Agent.git`. Commit and push after every phase.
  Sole author/contributor is `kshitij189`; no Claude co-author or attribution trailers.
- **D4 Temporal — RESOLVED:** run our own Temporal in this project's Compose on separate host ports
  (not 7233/8233, which another project uses). Port numbers chosen in Phase 5.

## Outstanding decisions
None.

## Blockers
None currently.
