# Implementation Progress

Status values: `NOT_STARTED` · `IN_PROGRESS` · `BLOCKED` · `COMPLETED`.
A phase is `COMPLETED` only when its acceptance criteria are met and the evidence below supports it.

**Repository inspection (2026-10-12):** the repository was empty — no source code, tests, or
configuration existed. No phase has verifiable prior work.

**Last completed phase:** Phase 2 — Schema normalization (2026-10-12).
**Next phase to implement:** Phase 3 — LLM mapping proposals (awaiting user authorization; live-LLM
evidence needs the keys in `.env`).

| # | Phase | Status |
|---|---|---|
| 1 | Mock CMS | COMPLETED |
| 2 | Schema normalization | COMPLETED |
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
**Status:** COMPLETED (2026-10-12)
**Objective:** Pydantic models for source fields, target fields, canonical entities, mappings and
transformations; normalize multiple firms' schemas into one representation.
**Depends on:** Phase 1 — satisfied.

Tasks / deliverables
- [x] Backend scaffold `backend/` (FastAPI, SQLAlchemy, Alembic `0001`): firms, cms_connectors,
      onboarding_projects, schema_definitions, field_definitions; composite FKs keep projects,
      connectors and schemas inside one firm
- [x] `NormalizedSchema` / `NormalizedField` common representation with fingerprinting
      (`app/schemas/normalized.py`)
- [x] Canonical model (Client, Matter, Intake, Task, DocumentReference, each with `extensions`) and
      the mappable `CANONICAL_ATTRIBUTES` registry (`app/domain/canonical.py`)
- [x] Source parsers: JSON Schema (Firm A), CSV field export (Firm B), HTML form (Firm C), with
      structured `schema_parse_error` details and a 1 MB limit
- [x] Target normalization via `integrations/mock_cms.py` (JSON API only): vendor→canonical map,
      vendor-specific fields kept with `canonical_ref: null` + extensions, origin allow-list,
      env-resolved credentials, bounded retry on transient errors, classified connector errors
- [x] Fixtures: `fixtures/source-schemas/firm-{a,b,c}`, `fixtures/target-schemas/mock-cms-default.json`
      (captured live), `fixtures/expected-mappings/firm-{a,b,c}.json` + README (author-labelled)
- [x] Endpoints: firms, connectors, `POST /onboarding-projects`, `GET /onboarding-projects/{id}`
      (with stage), `POST …/source-schema`, `POST …/inspect-target`, `GET …/schemas[/{id}]`
- [x] Structured JSON logging with request ids (`app/observability/logging.py`)
- Not built (deliberately): a generic `api_cms.py` — no second CMS API exists to justify it yet.

Acceptance criteria — all met
- [x] Firms A, B, C normalize consistently (equivalent concepts → same types; tested per concept).
- [x] Versioned schema definitions stored (immutable versions, idempotent re-registration,
      archived source documents, DB round-trip preserves the fingerprint).
- [x] Vendor-specific fields preserved in extensions (source: `x-*` keywords, CSV `legacy_id`,
      HTML `data-*` and option labels; target: custom fields and `callback_preference`).

Validation evidence (2026-10-12)
- `backend: pytest` — **57 passed** (unit: parsers, consistency, adapter; integration against
  PostgreSQL `onboarding_test`; 5 `live_cms` tests against the running mock CMS container, all
  passed, none skipped).
- Mutation check: removing the same-firm connector filter and the fingerprint idempotency check →
  5 failures; restored → 57 passed.
- `ruff check`, `ruff format --check`, `mypy --strict app tests` (41 files): clean.
- `mock-cms: pytest` re-run: 75 passed (unchanged).
- Manual run: backend under uvicorn on :8200 + mock CMS on :8100. For firms A/B/C: source upload
  201 (v1), identical repeat 200 `created: false`, inspect-target 201 at CMS revision 0, stage
  `ready_for_mapping`; malformed HTML → 422 `schema_parse_error`. `psql` on `onboarding` shows 6
  schema versions × 10 fields and archived paths `projects/<id>/source-v1-<fp>.<ext>`. Logs are
  JSON lines with request ids.

Limitations — follow-up of 2026-10-13 (requested by the user)
- [x] ~~No end-user authentication~~ → **closed.** Reviewer accounts (D5): `users` + `auth_tokens`
      (migration `0002`), roles `firm_admin` / `integration_specialist`, scrypt passwords, hashed
      bearer tokens with expiry, self-service firm sign-up (can be disabled), every endpoint
      firm-scoped (other firms' resources → 404), `created_by` recorded on projects and schema
      versions with same-firm composite FKs.
- [x] ~~No backend container~~ → **closed.** `backend/Dockerfile` (non-root, migrates on start);
      Compose `backend` service on :8200 reading the git-ignored `.env`; connectors reach the mock
      CMS as `http://mock-cms:8100`.
- [x] ~~No OpenTelemetry tracing~~ → **closed.** FastAPI, SQLAlchemy and httpx instrumentation,
      named domain spans (`normalize_source_schema`, `inspect_target`), `trace_id` in JSON logs,
      exporters none/console/otlp; opt-in Jaeger (`--profile observability`).
- [ ] Ground-truth labels are the author's judgement on synthetic data — inherent; can only be
      reduced by a human (the user) reviewing `fixtures/expected-mappings/`.

Follow-up evidence (2026-10-13)
- `backend: pytest` — **70 passed**, 0 skipped (13 new: auth/RBAC/tenant isolation, observability,
  seed). `mock-cms: pytest` — 75 passed. ruff + `mypy --strict` (51 files): clean.
- Mutation check: removing the firm filter in `get_project` and the permission check in
  `require()` → 2 targeted failures; restored → 70 passed.
- Containers: `docker compose up -d --build backend` builds and serves; migrations `0002→base→0002`
  ran in the container; seed created 3 firms / 6 reviewers / 3 connectors.
- Container walkthrough: no token → 401; specialist creating a project → 403; firm C upload (201,
  `created_by` c.specialist) and inspect-target via `http://mock-cms:8100` (201, tenant firm-c,
  revision 0) → `ready_for_mapping`; firm A specialist reading firm C's project → 404.
- Jaeger (OTLP): 11 traces; the inspect-target trace contains the server span, `inspect_target`,
  outgoing POST (token) and GET (schema) to the CMS, and SQL spans. Backend logs show `trace_id`.
- Afterwards the stack was returned to defaults (exporter `none`, Jaeger stopped). The dev
  `onboarding` DB was reset and reseeded (its earlier walkthrough rows predated authentication).

## Phase 3 — LLM mapping proposals
**Status:** NOT_STARTED
**Objective:** LLM proposes mappings via structured tool calls; deterministic validator accepts,
rejects or marks ambiguous. Approval and execution stay separate.
**Depends on:** Phase 2 — satisfied. Inputs: normalized source/target schema versions per project;
evaluate proposals against `fixtures/expected-mappings/`.

Tasks / deliverables
- [x] Reviewer authentication per D5 (delivered early in the Phase 2 follow-up; add an
      approval permission for `integration_specialist`)
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

- **D5 Reviewer authentication — RESOLVED (2026-10-13):** firm-scoped `users` with roles
  `firm_admin` / `integration_specialist`, hashed bearer tokens, no SSO. Implemented.
- **D6 LLM keys — RESOLVED (2026-10-13):** the user will put `GEMINI_API_KEY` and `GROQ_API_KEY` in
  the git-ignored root `.env` (created empty). Until filled, Phase 3 live-LLM evidence is BLOCKED.

## Outstanding decisions
None.
## Blockers
None for completed work. Phase 3 live-LLM evidence depends on D6.

## Next steps
1. User fills `GEMINI_API_KEY` / `GROQ_API_KEY` in the root `.env`.
2. User to authorize Phase 3 — LLM mapping proposals.