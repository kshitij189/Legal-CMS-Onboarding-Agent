# Implementation Progress

Status values: `NOT_STARTED` · `IN_PROGRESS` · `BLOCKED` · `COMPLETED`.
A phase is `COMPLETED` only when its acceptance criteria are met and the evidence below supports it.

**Repository inspection (2026-10-12):** the repository was empty — no source code, tests, or
configuration existed. No phase has verifiable prior work.

**Last completed phase:** Phase 5 — Durable orchestration (2026-10-13).
**Next phase to implement:** Phase 6 — Evaluation + dashboard (awaiting user authorization).

| # | Phase | Status |
|---|---|---|
| 1 | Mock CMS | COMPLETED |
| 2 | Schema normalization | COMPLETED |
| 3 | LLM mapping proposals | COMPLETED |
| 4 | Playwright execution | COMPLETED |
| 5 | Durable orchestration (Temporal) | COMPLETED |
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
**Status:** COMPLETED (2026-10-13)
**Objective:** LLM proposes mappings via structured tool calls; deterministic validator accepts,
rejects or marks ambiguous. Approval and execution stay separate.
**Depends on:** Phase 2 — satisfied.

Tasks / deliverables
- [x] Reviewer authentication per D5 (Phase 2 follow-up) + new permissions `mappings:propose`,
      `mappings:review`, `mappings:approve`, `audit:read` for `integration_specialist`
- [x] Provider-neutral LLM client `agent/llm_client.py`: Gemini (`google-genai`, default
      `gemini-3.8-flash`) → Groq (`openai/gpt-oss-120b`, httpx) fallback; forced single tool call,
      temperature 0, Pydantic-validated arguments, classified errors, bounded retries, Groq
      `Retry-After` wait (≤ 20 s), recorded fallbacks
- [x] Typed tool registry (`agent/tool_registry.py`) with only `propose_field_mappings`
      (`writes=False`); versioned prompts (`agent/prompts.py`, `mapping-v1`)
- [x] Tables (migration `0003`): `proposal_generations`, `mapping_proposals`, `mapping_versions`,
      `audit_events`; same-firm composite FKs; DB triggers make version and proposal content
      immutable and audit events append-only
- [x] Deterministic `domain/mapping_validator.py`: unknown source/target fields, duplicates,
      unsupported/incompatible transformations, type compatibility, value-map checks (incl.
      transformation-before-lookup), custom-field rules, unauthorized configuration area
      (`matter.intake_status`), low confidence, duplicate targets, required-target coverage,
      skipped fields, untrusted instruction text
- [x] Endpoints: `POST …/propose-mappings` (Idempotency-Key), `GET …/mappings`,
      `POST …/mappings/{id}/review`, `POST …/approve-mappings`, `GET …/mapping-versions[/{id}]`,
      `GET …/audit-events`
- [x] Prompt-injection handling: delimited untrusted JSON, cleaning/truncation, deterministic
      instruction detection forcing review

Acceptance criteria — all met
- [x] Proposals are validated deterministically (pure validator; statuses stored per proposal;
      final approved set re-validated as a whole).
- [x] Approval references a version: `approve-mappings` creates an immutable `mapping_versions`
      row (version number, fingerprint, pinned source/target schema ids); schema drift → 409.
- [x] No execution path from a proposal without approval: no execute/apply endpoint exists, the
      LLM tool registry has no write tools (both asserted by tests).

Validation evidence (2026-10-13)
- `backend: pytest` — **141 passed**, 1 deselected (`live_llm`, opt-in). `mock-cms` — 75 passed.
  ruff + `mypy --strict` (69 files): clean. `alembic check`: models match migrations.
- Validator: 14 parametrised rejection classes + 8 review classes + ground-truth tests.
- Mocked-LLM API flow: propose → review (approve/reject/edit/clarify rules) → approve → v1;
  idempotent re-approval; supersession by v2; schema-drift 409; duplicate targets 422; LLM
  failure 502 recorded as failed generation + audit event; skipped field placeholder; cross-firm
  404; DB immutability triggers.
- Mutation check: allowing approval of invalid proposals, skipping the schema-drift check, and
  downgrading unknown-target errors → each caught by tests; restored → all pass.
- **Live LLM run, Gemini 3.8 Flash primary** (`docs/evidence/phase3-live-llm-run-gemini-3.8-flash.json`,
  2026-10-13 07:15 UTC, timeout 240 s):
  | Firm | Provider | Latency | Validation (valid/review/invalid) | Agreement with labels | Clearly mappable |
  |---|---|---|---|---|---|
  | A | gemini-3.8-flash | 171 s | 9 / 1 / 0 | 9/10 | 8/8 |
  | B | groq gpt-oss-120b (Gemini timed out → fallback) | 5 s | 9 / 1 / 0 | 9/10 | 8/8 |
  | C | gemini-3.8-flash | 212 s | 7 / 3 / 0 | 7/10 | 7/7 |
  All disagreements are judgement calls that the validator routed to human review
  (`requires_custom_field` instead of `no_target` ×3; `review_state` proposed as ambiguous with
  `matter.intake_status` as candidate — which the validator would reject as unauthorized). The
  injected `notes` label was not obeyed: the model mapped by meaning, flagged the text in
  `open_questions`, and the validator forced review (`untrusted_instruction_text`).
  Earlier runs the same day: Groq-only (Gemini 400 → fixed by stripping unsupported schema
  keywords; Gemini quota) gave A 9/10, B 10/10, C 7/10. Synthetic data; not proof of real-world
  accuracy.
- **Model comparison and default change (2026-10-13 follow-up):** Gemini 3.5 Flash Lite (500
  req/day) — ~2.5 s, 22/23 clearly mappable, 5 invalid proposals (4 custom-field proposals missing
  name/entity/type, 1 value map on a text field), all rejected by the validator. Groq gpt-oss-120b
  — 23/23, 0 invalid, 26/30 agreement. The user chose Groq primary / Gemini Flash Lite backup.
  Final run with the new defaults (`docs/evidence/phase3-live-llm-run.json`): Groq for all firms,
  no fallback, A 9/10, B 10/10, C 7/10, 0 invalid; firm C waited out a short Groq rate limit
  (19.7 s). `backend: pytest` — 142 passed.

Known limitations
- Groq free tier: 8K tokens/min (~2 proposals/min); bursts wait once (≤ 20 s) or fall back to
  Gemini Flash Lite, whose proposals are weaker (validator catches the errors; more manual edits).
- The Gemini key belongs to the shared Google Cloud project "voice-agents"; another app consumes
  the same quota. A dedicated project/key is recommended.
- Proposal generation is synchronous inside the HTTP request (minutes with Gemini). Durable,
  asynchronous runs arrive with Temporal (Phase 5).
- Custom-field proposals are approved into the plan but not created anywhere yet (Phase 4).
- OpenTelemetry records LLM provider/model/token counts, not prompts; no Langfuse (optional, not
  opted in).

## Phase 4 — Playwright execution
**Status:** COMPLETED (2026-10-13)
**Objective:** Execute an approved configuration in the mock CMS (API and/or UI) and read it back.
**Depends on:** Phases 1–3 — satisfied.

Tasks / deliverables
- [x] `integrations/browser_cms.py`: Playwright adapter — exact label / role+name locators that
      must match exactly once (else `ui_drift`, stop), origin allow-list on every request **and**
      on redirects (fetched without following; off-origin redirects refused), page-origin check
      after each navigation, `trace.zip` + `failure.png` on failure only
- [x] API writer/reader in `integrations/mock_cms.py` (single-attempt writes; lost responses are
      `uncertain`); `ConfigurationReader` / `ConfigurationWriter` protocols and factories
- [x] Execution service with typed plan (`domain/execution_plan.py`), permission check
      (`runs:execute` + connector operations), idempotency keys, one running run per project,
      drift pre-flight, read-before-write, read-back-before-retry, stop at first failure;
      tables `onboarding_runs`, `action_steps`, `verification_results` (migration `0004`)
- [x] Read-after-write verification decides run status (`succeeded` / `verification_failed` /
      `failed` / `blocked`)
- [x] Mock CMS UI-variant switch `MOCK_CMS_UI_VARIANT` (v2 renames controls)
- [x] Endpoints `POST …/execute`, `GET …/runs[/{id}]`, `GET …/verification`
- [x] Backend image ships Chromium; Compose passes the UI variant

Acceptance criteria — all met
- [x] Approved plan applied and verified by read-back: Firm A via the real browser UI — 8 applied,
      8/8 verified, independently confirmed through the CMS API.
- [x] Missing/renamed element stops safely: UI v2 — first step `ui_drift` (`control_missing:
      Source field key`), remaining steps skipped, CMS revision unchanged (0), trace + screenshot.

Validation evidence (2026-10-13)
- `backend: pytest` — **172 passed**, 1 deselected (live LLM). Includes 7 e2e browser tests
  against real mock CMS processes (isolated `mock_cms_e2e` DB); e2e set repeated 3× — 7/7 each.
  `mock-cms: pytest` — 76 passed. ruff, `mypy --strict` (80 files), `alembic check`: clean.
- Firm A verification report: `docs/evidence/phase4-firm-a-browser-run.json` (browser channel,
  revisions 0 → 8, all 8 checks `match`). Re-run of the same version: 8 `already_applied`, CMS
  revision unchanged.
- Firm C: custom field `matter.custom.review_status` (enum, required) created before the mapping
  that targets it.
- Fault injection (in-memory CMS): lost response after success resolved by read-back (written once);
  lost write retried once; persistent uncertainty fails after 2 attempts; validation not retried;
  target drift blocks with zero writes; schema change / superseded version → 409; second active
  run → 409; unauthorized connector operation → 403; cross-firm → 404.
- Mutation check: disabling the already-applied skip, the blocking-drift check, or the read-back
  before retry each failed exactly its targeted test; restored → all pass.
- Container: backend image rebuilt with Chromium (2 GB); migration `0004` at head; inside the
  container Chromium logged in to `http://mock-cms:8100` (read-only check, demo data untouched) and
  navigation to `https://example.com/` was refused (`security` / `navigation_blocked`).

Bugs found and fixed during the phase
- Login waited for page load *after* the click (race); failed in the container, passed locally.
  Now waits for the navigation the click triggers.
- Redirects bypassed Playwright request routing, so an allowed page redirecting off-site made the
  browser contact the outside host (seen as a network-dependent flaky test). The guard now
  fetches allowed requests without following redirects and refuses off-origin targets.

Known limitations
- Runs are synchronous inside the HTTP request; a process crash mid-run leaves the run `running`
  and blocks the project until Phase 5 adds durable orchestration and recovery.
- Approved plans contain mappings and custom fields only (no field requirement changes yet).
- Browser channel is slower (~10–30 s per run) than the API channel; both verify via the API.

## Phase 5 — Durable orchestration
**Status:** COMPLETED (2026-10-13)
**Objective:** Temporal workflows for onboarding runs, retries and recovery.
**Depends on:** Phase 4 — satisfied.

Tasks / deliverables
- [x] Temporal in Compose on its own ports (D4): `temporalio/temporal:1.9.1` dev server, gRPC
      127.0.0.1:7234, UI 127.0.0.1:8234, SQLite in volume `temporal-data`, non-root
- [x] Worker service (`python -m app.workflows.worker`, Compose `worker`) with start-up recovery
- [x] Workflow `OnboardingRunWorkflow` + activities (preflight, pending steps, per-step execute
      with heartbeats, finalize, fail_run); bounded retry policy (5 attempts, backoff)
- [x] Execution service split into resumable stages; attempt recorded before each write;
      interrupted-after-write steps resolved by read-back (`after_interruption`)
- [x] `POST /execute` returns 202 and runs in the background; `workflow_id` per run (migration
      `0005`, unique); orchestrator unreachable → run marked failed + 503 (never left locked)
- [x] Uncertain-outcome read-before-retry (Phase 4) preserved inside the activity
- [ ] Approval signal — **not built, by decision**: approvals are durable database records bound
      to immutable versions, and a workflow is only started from an approved version, so a signal
      would duplicate the gate without adding safety.

Acceptance criteria — met
- [x] Interrupted work resumes from persisted state without repeating completed side effects.

Validation evidence (2026-10-13)
- Worker killed right after a write (real process, fault injection after creating
  `matter.custom.review_status`, browser channel): worker exits 70; step left `pending` with
  1 recorded attempt while the field exists in the CMS; a new worker resumes after the missed
  heartbeat; step `applied` with `after_interruption`, still 1 attempt; run `succeeded`, 9/9
  verified, CMS revision exactly 9.
- Mutation: disabling read-before-write makes the resumed worker create the field again → CMS
  rejects → test fails. Restored → passes.
- Duplicate execution: same Idempotency-Key → same run; second workflow start for the same run
  rejected (running and after completion); new run of the same version → 8 `already_applied`,
  CMS revision unchanged.
- Recovery: run saved without a workflow (simulated API crash) is started by worker start-up and
  succeeds.
- Background run: 202 → `succeeded`; Temporal reports the workflow COMPLETED.
- Full-stack containers (`docs/evidence/phase5-worker-kill-run.json`): Firm B, proposals by Groq,
  9 approved items (10 steps, browser channel); worker **container** killed after 2 steps, run
  stayed `running` (2 applied / 8 pending); worker restarted; run `succeeded`, 10/10 verified,
  every step 1 attempt, CMS revision 0 → 10 (independent read).
- `backend: pytest` — **176 passed**, 1 deselected (live LLM). ruff, `mypy --strict` (87 files),
  migrations up/down: clean. `mock-cms: pytest` — 76 passed.

Bugs found and fixed during the phase
- `temporalio` was installed locally but missing from `pyproject.toml`; containers crashed on
  import. Declared; noted in CLAUDE.md that the container build is the check.
- Temporal volume was root-owned and the image runs as `temporal`; mounted at the user's home
  instead of running as root.
- Unnamed unique constraint made migration `0005` irreversible; named it (dev DB repaired).

Known limitations (deferred by the user to after Phase 5, unless noted)
- Plans cover mappings and custom fields only (no required-field changes).
- Temporal dev server (SQLite) is for local use; production would need a clustered Temporal.
- A crash that kills the *Temporal server* itself relies on its SQLite persistence (not tested).

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
  **Revised 2026-10-13 by the user:** Groq (`openai/gpt-oss-120b`) primary, Gemini
  (`gemini-3.5-flash-lite`) backup, after a live comparison of three models — see
  `docs/evidence/README.md`.
- **D2 Blueprint in repo — RESOLVED:** committed as [`docs/BLUEPRINT.md`](docs/BLUEPRINT.md).
- **D3 Version control — RESOLVED:** git repo, remote
  `https://github.com/kshitij189/Legal-CMS-Onboarding-Agent.git`. Commit and push after every phase.
  Sole author/contributor is `kshitij189`; no Claude co-author or attribution trailers.
- **D4 Temporal — RESOLVED:** run our own Temporal in this project's Compose on separate host ports
  (not 7233/8233, which another project uses). Implemented on 7234 (gRPC) / 8234 (UI).

- **D5 Reviewer authentication — RESOLVED (2026-10-13):** firm-scoped `users` with roles
  `firm_admin` / `integration_specialist`, hashed bearer tokens, no SSO. Implemented.
- **D6 LLM keys — RESOLVED (2026-10-13):** the user will put `GEMINI_API_KEY` and `GROQ_API_KEY` in
  the git-ignored root `.env` (created empty). Until filled, Phase 3 live-LLM evidence is BLOCKED.

## Outstanding decisions
None.
## Blockers
None for completed work. Phase 3 live-LLM evidence depends on D6.

## Next steps
1. User to authorize Phase 6 — evaluation suite (30–50 labelled scenarios, one command) and the
   Next.js dashboard.
2. Revisit the limitations the user deferred: required-field changes in approved plans.
