# Change Log

Chronological engineering log. Newest entries at the bottom of each phase section. Only verified work
and actual test results are recorded.

## 2026-10-12 — Project documentation setup

**Changes**
- Inspected repository: empty directory, not a git repository, no prior implementation.
- Created `CLAUDE.md` (engineering instructions), `IMPLEMENTATION_PROGRESS.md` (phase checklist),
  `CHANGE_LOG.md` (this file).

**Decisions**
- Python 3.13 chosen over the also-installed 3.14 for dependency wheel availability.
- Compose Postgres on host port 5434, because a native PostgreSQL 17 service already occupies 5432 and
  its credentials are unknown, and another project's container (`legal_intake_agent`) holds 5433
  (first attempt at 5433 failed with "port is already allocated").
- `mock-cms/` and `backend/` are independent Python projects with separate virtualenvs (both contain a
  package named `app` per the blueprint layout; the mock CMS also stands in for an external vendor and
  must not share code with the backend).
- "Claude" references in the blueprint are treated as "the LLM"; provider choice recorded as open
  decision D1.

**Tests:** none (documentation only).


---

## Phase 1 — Mock CMS

### 2026-10-12 — Mock legal CMS implemented (status: COMPLETED)

**Added**
- Infrastructure: `.gitignore`, `.env.example`, `docker-compose.yml` (`postgres:17` on host 5434,
  `mock-cms` on 8100), `docker/postgres/init/01-create-databases.sql` (`mock_cms`, `mock_cms_test`).
- `mock-cms/` service (independent Python project, own venv):
  - `app/catalog.py` — entities, data types, transformations and their compatible target types,
    10 built-in fields (contact + matter).
  - `app/models.py` + Alembic `0001` — tenants (with `config_revision`), users, auth_sessions,
    field_definitions, intake_mappings, intake_records. Check constraints on enums; composite FKs
    `(tenant_id, target_field_id)` and `(tenant_id, submitted_by_id)` prevent cross-tenant references
    even via raw SQL.
  - `app/security.py` — stdlib scrypt password hashing, random tokens stored as SHA-256 hashes.
  - `app/permissions.py` — 4 roles × 6 permissions; `integration` cannot read/create client records.
  - `app/services/` — auth, configuration (field updates, custom fields, mapping upsert/delete with
    deterministic validation and idempotent no-op detection), intake (transformations, type coercion,
    required-field checks, persistence).
  - `app/api.py` — JSON API `/api/v1` with `{"error": {code, message, details}}` envelope.
  - `app/ui.py` + `templates/` — server-rendered UI with accessible labels, CSRF-protected forms.
  - `app/seed.py` + `fixtures/demo_firms.json` — firms A/B/C, one user per role, idempotent.
  - `Dockerfile` (non-root, runs migrations on start), `.dockerignore`.
  - `tests/` — 75 tests.
- `CLAUDE.md`: commands updated, mock CMS contract section, occupied local ports.

**Decisions and rationale**
- *Mock CMS is a separate service talking HTTP only*: it simulates an external vendor, so the
  backend must integrate with it exactly as it would with a real CMS.
- *Same stack as the backend (FastAPI, SQLAlchemy, Alembic, PostgreSQL)*: no extra frameworks; state is
  real and inspectable with `psql`, which the Phase 1 exit criterion requires.
- *Jinja2 server-rendered UI* rather than a SPA: gives Playwright a real, stable, accessible UI to
  drive in Phase 4 without adding a JS toolchain to the mock.
- *Intake mappings as the CMS's configurable integration surface*: this is what the onboarding agent
  will configure (source key → CMS field + transformation + enum value map), matching blueprint §2/§6.
- *`config_revision` + `changed` flag on writes*: gives later phases a deterministic way to detect drift,
  verify read-after-write, and make retries idempotent (blueprint §14).
- *Stdlib scrypt/secrets instead of passlib/JWT libraries*: avoids dependencies; tokens are opaque and
  revocable server-side.
- *JSONB with `none_as_null=True`*: found during manual verification that absent `value_map` was stored
  as JSON `null`; fixed so `IS NULL` queries are correct (regression assertion added).

**Dependencies added (mock-cms)**
fastapi 0.143.0, uvicorn 0.54.0, SQLAlchemy 2.0.54, psycopg[binary] 3.3.6, alembic 1.20.0,
pydantic 2.14.1, Jinja2 3.1.6 (UI templates), python-multipart 0.0.32 (HTML form parsing);
dev: pytest 9.1.1, httpx 0.28.1 (TestClient), ruff 0.17.0, mypy 2.4.0. All within the approved stack.

**Tests executed (actual results)**
- `python -m pytest`: 75 passed, 1 third-party deprecation warning (Starlette suggests `httpx2` for
  `TestClient`).
- Mutation check (temporary): staff given `fields:write` + CSRF check disabled → 4 failed / 71 passed;
  reverted → 75 passed.
- `ruff check .` clean; `ruff format --check .` clean; `mypy --strict app tests` no issues.
- `docker compose up -d --build mock-cms`: builds and serves; `/healthz` ok, `/login` 200.
- Manual walkthrough against the container (UI + API configuration, API read-back, `psql`
  verification, 403 for integration-role intake submission, 422 structured validation errors) —
  results recorded in `IMPLEMENTATION_PROGRESS.md`. Dev DB reset and reseeded afterwards.

**Bugs fixed**
- JSON `null` stored for absent JSONB values (see decisions).
- First Compose start failed on port 5433 (occupied) → moved to 5434.

**Known issues / limitations**
- UI-variant switch for drift testing not yet implemented (planned for Phase 4).
- No login rate limiting; async HTML intake handler performs sync DB calls (acceptable for a local mock).
- FastAPI 0.143 lists included routers as nested route entries, so `len(app.routes)` is not a route
  count — informational only.


---

## 2026-10-12 — Project decisions D1–D4 and version control

- D1: Gemini primary (`google-genai`), Groq optional fallback, module `llm_client.py`.
- D2: blueprint committed as `docs/BLUEPRINT.md`.
- D3: git repository initialised; remote `https://github.com/kshitij189/Legal-CMS-Onboarding-Agent.git`;
  commit + push after every phase, authored solely by `kshitij189` (no Claude attribution).
- D4: Temporal will run in this project's Compose on its own host ports (Phase 5).
- Phase 1 work committed and pushed as the initial commit.


---

## Phase 2 — Schema normalization

### 2026-10-12 — Normalization, canonical model and schema registry (status: COMPLETED)

**Added**
- `backend/` (independent Python project, own venv; Alembic `0001`): firms, cms_connectors,
  onboarding_projects, schema_definitions, field_definitions with composite same-firm FKs.
- `app/schemas/normalized.py` — `NormalizedSchema`/`NormalizedField`, `DataType` incl. `unsupported`,
  content fingerprint for versioning.
- `app/domain/canonical.py` — Client, Matter, Intake, Task, DocumentReference (+ `extensions`) and
  the mappable canonical attribute registry.
- `app/domain/parsers/` — JSON Schema, CSV field export and HTML form parsers;
  `app/domain/schema_normalizer.py` dispatcher with a 1 MB limit.
- `app/integrations/` — adapter protocol (`base.py`), mock CMS API adapter (`mock_cms.py`),
  env-based secret resolution (`secrets.py`), adapter registry.
- `app/domain/project_service.py` + `app/api/{firms,onboarding,schemas}.py` — project, connector
  and schema-version endpoints; source documents archived to `artifacts/`.
- `app/observability/logging.py` — JSON logs with `x-request-id` correlation.
- Fixtures: three firm source schemas, live-captured target snapshot, ground-truth expected mappings.
- `docs/BLUEPRINT.md`, `.gitattributes`; `.env.example` and Postgres init script extended for the
  backend; CLAUDE.md commands, normalization contract and git rules updated.

**Decisions and rationale**
- *One normalized representation for source and target*: later phases compare like with like and
  never touch vendor formats; vendor/format specifics live in `extensions`.
- *Parsers never infer meaning*: no name-based guessing (e.g. "mobile" → phone) or canonical refs on
  source fields — semantic mapping is the LLM's job (Phase 3) and must be validated, not hidden in a
  parser. Explicit type signals (`format: phone`, `type=tel`, "Phone" column type) are used.
- *Each firm uses a different source format* (JSON Schema / CSV export / HTML form) so the three
  fixtures exercise all three parsers and prove cross-format consistency.
- *Fingerprint-based immutable versions*: identical re-submission is idempotent; any change yields a
  new version, which later lets approvals bind to exact schema versions (blueprint §14).
- *Archived uploads use server-generated paths*; the client filename is metadata only.
- *Credentials by reference* (`credential_ref` → env vars), never in the DB or API responses; the
  connector uses the mock CMS `integration` account (least privilege).
- *Read-only retries only*: the adapter retries transient failures on GETs/login (3 attempts,
  backoff); authorization/contract errors stop immediately with a category.
- *`jsonschema` dependency*: validates uploaded JSON Schemas against their meta-schema (approved
  "JSON Schema" layer of the stack). HTML parsing uses the standard library.
- *No generic `api_cms.py` yet*: there is only one CMS API; building a generic connector now would be
  speculative.

**Dependencies added (backend)**
fastapi 0.143.0, uvicorn 0.54.0, SQLAlchemy 2.0.54, psycopg[binary] 3.3.6, alembic 1.20.0,
pydantic 2.14.1, httpx 0.28.1 (adapter HTTP client), jsonschema 4.26.0;
dev: pytest 9.1.1, ruff 0.17.0, mypy 2.4.0, types-jsonschema.

**Tests executed (actual results)**
- `backend: python -m pytest` — 57 passed (incl. 5 `live_cms` tests against the running mock CMS;
  0 skipped). One third-party deprecation warning (Starlette `TestClient`/`httpx`).
- Mutation check (temporary): same-firm connector filter removed + fingerprint idempotency disabled
  → 5 failed / 52 passed; reverted → 57 passed.
- `ruff check`, `ruff format --check`, `mypy --strict app tests`: clean.
- `mock-cms: python -m pytest` — 75 passed.
- Manual uvicorn run for firms A/B/C with `psql` verification — see `IMPLEMENTATION_PROGRESS.md`.

**Bugs fixed during the phase**
- HTML parser labelled radio groups with the first option's text; the `<legend>` now wins.
- HTML select option display text (e.g. `DIV` → "Divorce & Custody") was lost; kept in
  `extensions.option_labels`.
- JSON Schema top-level `description` was discarded; now preserved in schema extensions.
- Unnamed submit buttons were reported as "control has no name"; now "not an intake field".

**Known issues / limitations**
- No end-user authentication on the backend API yet (decision D5, before Phase 3 approvals).
- No backend container; OpenTelemetry tracing not yet added.
- In the manual run, my PowerShell health-poll loop reported the server as not up although every
  subsequent request succeeded — a script quirk, not an application issue.

### 2026-10-13 — Phase 2 follow-up: limitations closed (authentication, container, tracing)

Requested by the user after Phase 2. Decisions D5 (reviewer login) and D6 (LLM keys via `.env`)
confirmed.

**Added / changed**
- Root `.env` created (git-ignored) with empty `GEMINI_API_KEY` / `GROQ_API_KEY`; the backend
  loads it for local runs (`app.config.load_dotenv`, never overriding existing variables) and
  Compose passes it to the backend container (`env_file`, optional).
- Reviewer authentication (D5): `users`, `auth_tokens` (migration `0002`), `app/security.py`
  (scrypt, hashed tokens), `app/permissions.py` (`firm_admin`, `integration_specialist`),
  `app/domain/auth_service.py`, `app/api/deps.py`, `app/api/auth.py` (`/auth/token`, `/auth/logout`,
  `/auth/me`). API reshaped around the caller's firm: `POST /firms` is sign-up (firm + first admin,
  `ONBOARDING_ALLOW_SIGNUP`), `GET /firm`, `GET|POST /users`, `GET|POST /connectors`; projects and
  schemas are looked up within the caller's firm (other firms → 404). `created_by_id` on projects
  and schema versions with same-firm composite FKs (nullable only for pre-auth rows).
- `app/seed.py`: demo firms A/B/C, `<x>.admin` / `<x>.specialist`, one mock CMS connector each.
- Tracing: `app/observability/tracing.py` (FastAPI, SQLAlchemy, httpx instrumentation; exporters
  none/console/otlp), domain spans `normalize_source_schema` and `inspect_target`, `trace_id` in
  JSON logs.
- Containers: `backend/Dockerfile` + `.dockerignore`; Compose `backend` (:8200) and opt-in
  `jaeger` (profile `observability`, :16686 UI, :4318 OTLP).
- `.env.example`, CLAUDE.md (commands, auth, secrets, observability rules) updated.

**Decisions and rationale**
- *Users belong to one firm* — tenant isolation follows from the authenticated user; a resource of
  another firm is reported as not found so its existence is not revealed.
- *Firm creation via sign-up* (firm + first firm admin in one transaction) — keeps exactly the two
  approved roles while allowing the blueprint's "create a firm in the dashboard"; switchable off.
- *Specialists cannot manage users, connectors or create projects*; they can upload schemas and
  inspect the target. Mapping approval will be added to their role in Phase 3.
- *Stdlib `.env` loader instead of python-dotenv* — a dozen lines, no dependency.
- *Jaeger behind a Compose profile* — tracing export is verifiable locally without adding an
  always-on service; default exporter is `none`.
- *httpx tracing is tested against a real local HTTP server* — instrumentation wraps the real
  network transport, not test `MockTransport`s.

**Dependencies added (backend)**
opentelemetry-api / -sdk / -exporter-otlp-proto-http 1.45.1, opentelemetry-instrumentation-fastapi /
-sqlalchemy / -httpx 0.66b1 (approved "OpenTelemetry" layer). Image: `jaegertracing/all-in-one:1.62.0`
(opt-in).

**Tests executed (actual results)**
- `backend: pytest` — 70 passed (0 skipped). `mock-cms: pytest` — 75 passed.
- ruff check / format: clean; `mypy --strict app tests` (51 files): clean.
- Mutation check: firm filter in `get_project` and permission check in `require()` disabled →
  2 failures; restored → 70 passed.
- Containers + Jaeger walkthrough — results in `IMPLEMENTATION_PROGRESS.md` (Phase 2 follow-up).

**Bugs fixed**
- First tracing test used `MockTransport`, which httpx instrumentation does not wrap — replaced
  with a real local server (test bug, not product bug).

**Known issues / limitations**
- Ground-truth expected mappings remain author-labelled; only a human review can reduce that.
- Dev `onboarding` DB was reset and reseeded; rows from the Phase 2 manual walkthrough were removed.


---

## Phase 3 — LLM mapping proposals

### 2026-10-13 — Proposals, deterministic validation, review and approval (status: COMPLETED)

**Added**
- `backend/app/agent/`: `llm_client.py` (Gemini + Groq clients, fallback chain, error
  classification), `tool_registry.py` (allow-list with only `propose_field_mappings`, schema
  inlining), `prompts.py` (`mapping-v1`, untrusted-data block, instruction detection),
  `mapping_agent.py`.
- `backend/app/schemas/mapping.py` (tool arguments, validation report), `schemas/mapping_api.py`.
- `backend/app/domain/mapping_validator.py` (pure validator), `mapping_service.py`
  (generate/review/approve), `audit.py`.
- Migration `0003`: `proposal_generations`, `mapping_proposals`, `mapping_versions`,
  `audit_events`, plus triggers enforcing immutability / append-only.
- `backend/app/api/mappings.py`: propose, list, review, approve, versions, audit events.
- Permissions `mappings:propose|review|approve`, `audit:read` (integration specialist; admin has all).
- Tests: `tests/unit/test_mapping_validator.py`, `tests/unit/test_agent.py`,
  `tests/integration/test_mappings_api.py`, `tests/integration/test_migrations.py`,
  opt-in `tests/live/test_live_llm.py`; shared `tests/mapping_helpers.py` (`FakeLLM`).
- Evidence: `docs/evidence/phase3-live-llm-run.json` (raw live proposals + scores).

**Decisions and rationale**
- *Forced tool calling instead of free-text JSON*: the provider must call the declared tool; its
  arguments are validated by Pydantic, so malformed output is rejected, never repaired by guessing.
- *Flat, null-free tool schema* (`value_map` as a list of pairs, custom-field fields inline):
  portable across Gemini and Groq tool dialects.
- *Gemini-only keyword stripping*: Gemini returned `400 INVALID_ARGUMENT` for the full schema;
  removing `additionalProperties`, `default`, `min/maxLength`, `min/maxItems` from the declared
  schema fixed it (verified live). Constraints remain enforced on the returned arguments.
- *Defaults `gemini-3.8-flash` / `openai/gpt-oss-120b`*: `gemini-2.5-flash` is no longer offered
  to new keys (API told us to use 3.8-flash); gpt-oss-120b supports forced tool calls on Groq.
- *Groq over httpx, not its SDK*: one request shape, no extra dependency.
- *Validator decides, model only proposes*: confidence can lower but never raise a status;
  `invalid` proposals cannot be approved; `ambiguous` must be edited; `needs_review` approvals
  need a comment; edits are new rows superseding the original (model output is never altered).
- *Skipped fields get explicit `system` placeholders* so nothing silently drops out of review.
- *Approval = immutable version pinned to schema versions*, fingerprinted; re-approval of the same
  content is idempotent; schema drift invalidates pending approvals (409).
- *Deterministic injection detection* in addition to prompt instructions: a model can be fooled,
  a regex flag routed to human review cannot.
- *Live LLM tests opt-in* (`-m live_llm`) because they spend limited free-tier quota.

**Dependencies added (backend)**
`google-genai` 2.29.0 (approved Gemini SDK). Groq is called via the existing `httpx`.

**Tests executed (actual results)**
- `backend: pytest` — 141 passed, 1 deselected (live). `mock-cms: pytest` — 75 passed.
- ruff, `mypy --strict` (69 files), `alembic check`: clean.
- Mutation check: three safety rules disabled one at a time → caught; restored → all pass.
- Live runs (2026-10-13): see `IMPLEMENTATION_PROGRESS.md` Phase 3 table. Final run: Gemini for
  firms A and C (171 s / 212 s), Groq fallback for B; 25/30 decisions agree with the labels,
  23/23 clearly mappable fields correct, 0 invalid proposals.

**Bugs found and fixed during the phase**
- Migration `0003` was generated before the `system` proposal origin existed; its CHECK constraint
  rejected placeholders (caught by an integration test). Fixed in the unreleased migration; added
  an `alembic check` test for model/migration drift (note: CHECK constraint text is not compared).
- Gemini 400 on the full tool schema (see decisions).
- Prompt rule for enum targets clarified to use transformation `none`: the CMS applies the
  transformation before the value-map lookup, so `lowercase` would break original-case keys.
- Groq per-minute rate limit failed the third request of a burst; added a single bounded
  `Retry-After` wait.

**Known issues / limitations**
- Gemini free tier quota (~20/day/model) and very high latency; requests often fall back to Groq.
- Proposal generation runs synchronously within the HTTP request (until Phase 5).
- Approved custom fields and mappings are not applied to any CMS yet (Phase 4).

### 2026-10-13 — Phase 3 follow-up: LLM provider comparison; Groq primary (decision D1 revised)

**Context.** The user asked why the Gemini model with the highest daily quota was not used. The
default `gemini-3.8-flash` had been taken from the API's own deprecation message, not chosen by
quota. The user supplied the project's AI Studio limits: 3.8/3.7/3.6 Flash 5 RPM / 20 RPD;
3.5 Flash Lite 15 RPM / 500 RPD (all 250K TPM). Groq's response headers showed 1,000 req/day and
8K tokens/min for `openai/gpt-oss-120b`.

**Measured (live, same prompt `mapping-v1`, firms A–C):**
- Gemini 3.5 Flash Lite: ~2.5 s per firm; 22/23 clearly mappable; 5 invalid proposals (4
  `requires_custom_field` without name/entity/type; 1 value map on a text target) — all rejected by
  the validator; 23/30 agreement.
- Gemini 3.8 Flash: 171–212 s; 0 invalid; 16/20 agreement on the two firms it answered.
- Groq gpt-oss-120b: ~4–20 s; 0 invalid; 26/30 agreement; 23/23 clearly mappable.

**Decision (user-approved).** `LLM_PROVIDERS` default `groq,gemini`; `GEMINI_MODEL` default
`gemini-3.5-flash-lite` (backup must be available: high quota, low latency). Rationale and data in
`docs/evidence/README.md`.

**Changed.** `app/agent/llm_client.py` defaults and docstrings; `app/api/mappings.py` docstring;
new unit test for the default order; `.env.example` (`LLM_PROVIDERS`, `GROQ_MODEL`, `GEMINI_MODEL`,
`LLM_TIMEOUT_SECONDS`); CLAUDE.md stack table and LLM rules; evidence files renamed per model plus
`docs/evidence/README.md`.

**Tests.** `backend: pytest` — 142 passed, 1 deselected; ruff and mypy clean. Live run with the new
defaults: Groq for all three firms, no fallback, 0 invalid, 26/30 agreement.

**Notes.** The Gemini key belongs to the Google Cloud project "voice-agents", whose quota is shared
with another application (279 Flash Lite requests that day were not from this project).


---

## Phase 4 — Playwright execution

### 2026-10-13 — Execute approved versions via browser or API, verify by read-back (status: COMPLETED)

**Added**
- `backend/app/integrations/browser_cms.py`: Playwright (sync) adapter — exact label / role+name
  locators that must match exactly once, origin allow-list on every request, trace + screenshot on
  failure, outcome read from the CMS's own status/alert notices.
- `backend/app/integrations/mock_cms.py`: `read_configuration`, `create_custom_field`,
  `upsert_mapping` over the API; writes are single-attempt and lost responses are `uncertain`.
- `backend/app/integrations/base.py` / `registry.py`: `ConfigurationReader` /
  `ConfigurationWriter` protocols, `build_reader`, `build_writer(channel)`.
- `backend/app/schemas/execution.py`, `backend/app/domain/execution_plan.py` (pure: plan, state,
  drift, verification), `backend/app/domain/execution_service.py` (runs), `backend/app/api/execution.py`
  (`POST /execute`, `GET /runs[/{id}]`, `GET /verification`).
- Migration `0004`: `onboarding_runs` (partial unique index: one running run per project),
  `action_steps`, `verification_results`; data update giving existing mock CMS connectors the
  operations `read_configuration`, `write_api`, `write_browser`.
- Permission `runs:execute` (integration specialist; admin has all). Settings
  `ONBOARDING_BROWSER_TIMEOUT_MS`, `ONBOARDING_BROWSER_HEADLESS`.
- Mock CMS: `MOCK_CMS_UI_VARIANT` (`v1` default, `v2` renames four controls) + test.
- Tests: `tests/unit/test_execution_plan.py`, `tests/integration/test_execution_api.py` (in-memory
  CMS with fault injection, `tests/execution_helpers.py`), `tests/e2e/test_browser_execution.py`
  (real mock CMS processes + Chromium). Evidence `docs/evidence/phase4-firm-a-browser-run.json`.
- Backend image installs Chromium (`playwright install --with-deps chromium`); Compose passes
  `MOCK_CMS_UI_VARIANT`. New database `mock_cms_e2e` (init script) for isolated browser tests.

**Decisions and rationale**
- *Plan derived only from the approved version*; the execute endpoint accepts nothing else.
- *Reads always via the documented API, writes via the run's channel*: verification must not
  depend on the same UI the writer used.
- *Read-before-write per step* makes re-runs idempotent (`already_applied`) without trusting
  client-side bookkeeping; *conflict* (target held by an unmanaged mapping, or a custom field that
  exists with a different definition) stops the run instead of overwriting.
- *Uncertain outcomes* (lost response, no UI confirmation, 5xx on a write) are resolved by reading
  state; at most one retry, only if the change is absent.
- *Drift pre-flight*: changes to fields the plan targets block the run before any write; other
  differences and unexpected fields are reported, preserved and untouched.
- *Stop at first failure*: later steps are `skipped`, never improvised; verification still runs
  so the report shows the real state.
- *Browser outcome from the CMS's own notices* (`role=status` / `role=alert`), not from clicks.
- *E2E harness with its own mock CMS processes and database*: realistic browser tests that never
  modify demo data; UI v2 on a second port demonstrates drift.

**Dependencies added (backend)**
`playwright` 1.63.0 (approved stack) + Chromium.

**Tests executed (actual results)**
- `backend: pytest` — 171 passed, 1 deselected (live LLM), incl. 6 e2e browser tests.
- `mock-cms: pytest` — 76 passed. ruff + `mypy --strict` (80 files) clean; `alembic check` clean.
- Mutation check: removing the already-applied skip, the blocking-drift check, and the
  read-back-before-retry each failed exactly its targeted test; restored → all pass.

**Bugs found and fixed during the phase**
- e2e tests could not see integration fixtures (added `tests/e2e/conftest.py`).
- A Phase 2 test asserted the old connector operation list (updated for the new operations).
- Steps attempted when a session-level error occurred are now `failed`, not `skipped`.
- Browser login waited for page load after the click (race). It passed locally but failed in the
  container; the adapter now waits for the navigation the click triggers.
- Security gap: Playwright does not route redirects, so an allowed page redirecting off-site made
  the browser contact the outside host (surfaced as a network-dependent flaky e2e test). The guard
  now fetches allowed requests without following redirects and refuses off-origin targets; the
  page origin is also checked after every navigation. Covered by an e2e test with a real
  redirecting server.
- Backend Dockerfile reordered so the Chromium layer is cached across code changes.

**Known issues / limitations**
- Runs execute synchronously inside the HTTP request (seconds for the API channel, ~5–30 s for the
  browser). A crash mid-run leaves the run `running` and blocks new runs for the project until
  Phase 5 (durable orchestration/recovery) — documented, not yet automated.
- Firm-level requirement changes (e.g. making a CMS field required) are not part of approved plans
  yet; only mappings and custom fields are executed.


---

## Phase 5 — Durable orchestration

### 2026-10-13 — Temporal workflows, resumable stages, crash recovery (status: COMPLETED)

**Added / changed**
- Compose: `temporal` (`temporalio/temporal:1.9.1` dev server, 127.0.0.1:7234 gRPC / :8234 UI,
  volume `temporal-data` at the non-root user's home) and `worker` (backend image,
  `python -m app.workflows.worker`, shares backend settings via YAML anchors).
- `backend/app/workflows/`: `onboarding_run.py` (workflow), `activities.py`, `client.py`
  (idempotent start, reuse policy `ALLOW_DUPLICATE_FAILED_ONLY`), `worker.py` (start-up recovery
  of runs without a workflow).
- `backend/app/domain/execution_service.py` split into stages: `create_run`, `preflight`,
  `pending_steps`, `execute_step`, `finalize`, `fail_run`, `run_inline`. Attempts committed before
  writes; steps re-read the CMS first and record interrupted-after-write steps as applied
  (`after_interruption`). Test-only fault injection `ONBOARDING_FAULT_CRASH_AFTER_WRITE`.
- `POST /execute`: creates the run, dispatches to Temporal (202) or inline; `workflow_id` in run
  output; orchestrator unreachable → run failed + 503 `orchestrator_unavailable`.
- Migration `0005`: `onboarding_runs.workflow_id` (unique, named constraint).
- Settings `ONBOARDING_ORCHESTRATOR`, `ONBOARDING_TEMPORAL_ADDRESS|NAMESPACE|TASK_QUEUE`.
- Tests: `tests/e2e/harness.py` (shared mock CMS harness, session-scoped), 
  `tests/e2e/test_durable_runs.py` (real Temporal + worker processes). Evidence
  `docs/evidence/phase5-worker-kill-run.json`.

**Decisions and rationale**
- *One activity per step* so a crash loses at most one in-flight step, and heartbeats let Temporal
  move it to a live worker quickly (20 s) instead of waiting for the step timeout.
- *Safety stays in the stages, not in Temporal*: Temporal guarantees the work is retried; the
  stages guarantee a retry never repeats a side effect (attempt recorded before writing +
  read-before-write).
- *Random workflow ids per run*: database resets cannot collide with old workflows in Temporal.
- *Approval signal not built*: approval is already a durable, immutable record checked before a
  workflow exists; a signal would be a second gate with no added safety.
- *Inline orchestrator kept* for tests and Temporal-less environments; both modes run the same
  stage functions, and the Temporal path has its own end-to-end tests.
- *Per-test task queues* in durable tests, so stray tasks from other runs are never consumed.

**Dependencies added (backend)** `temporalio` 1.34.0 (approved stack). Image
`temporalio/temporal:1.9.1` (already used on this machine by another project).

**Tests executed (actual results)**
- `backend: pytest` — 176 passed, 1 deselected (live LLM); 4 durable-run tests against real
  Temporal and worker processes. `mock-cms: pytest` — 76 passed. ruff + mypy clean.
- Mutation: read-before-write disabled → resumed worker re-creates the custom field → test fails.
- Full-stack containers: worker container killed mid browser run and restarted → run succeeded,
  10/10 verified, every step one attempt, CMS revision 0 → 10.

**Bugs found and fixed**
- `temporalio` missing from `pyproject.toml` (local venv masked it; containers crashed).
- Root-owned Temporal volume vs non-root image user (mount moved, not run as root).
- Unnamed unique constraint made `0005` irreversible (named; dev DB repaired with SQL).
- Phase 2/4 test expectations unaffected; durable test plan count corrected (9 actions for firm C).

**Known issues / limitations**
- Temporal dev server (SQLite) is a local setup, not production topology.
- Required-field changes in plans: deferred by the user.
- The demo databases now contain a completed Firm B onboarding from the container test
  (reset commands in CLAUDE.md).


---

## Phase 6 — Evaluation + dashboard

### 2026-10-13 — Evaluation suite (46 scenarios) and Next.js dashboard (status: COMPLETED)

**Added**
- `backend/app/evaluation/`: `simulation.py` (in-memory CMS with faults incl. crash-after-write and
  silent wrong save; scripted LLM — now shared with the tests), `fixtures.py`, `mapping_eval.py`
  (mapping and validator scenarios, scoring), `execution_eval.py` (16 execution-safety scenarios via
  the real API and stages), `llm_cache.py` (record/replay keyed by prompt hash), `runner.py`
  (metrics, JSON/Markdown report, exit code).
- `scripts/run_evaluations.py` (one command); `fixtures/evaluation/{mapping,validation}/` (30
  labelled scenario files) and `llm-cache.json` (recorded Groq answers, synthetic data only);
  database `onboarding_eval` (init script).
- Reports: `docs/evidence/evaluation-report.{json,md}`.
- `frontend/`: Next.js 15 + TypeScript dashboard (App Router, no UI library): sign-in / firm sign-up,
  projects, upload, inspect, propose, schemas, mapping review (approve/reject/edit/ask), versions,
  execute (browser/API), live run status with explained steps/drift/verification, audit log;
  plain-language explanations in `lib/explain.ts`; same-origin proxy to the backend.
- `GET /api/v1/onboarding-projects` (firm-scoped list) + test.
- `scripts/dashboard_walkthrough.py` (Playwright journeys with screenshots in
  `docs/evidence/phase6-dashboard/`).
- Tests: `tests/unit/test_evaluation.py` (scoring rules, record/replay, scenario integrity).

**Decisions and rationale**
- *Labels as outcomes, not exact answers*: `map` (exact target), `review` (anything but a valid
  mapping), `not_mapped` — measuring whether a human is involved when needed, and counting *valid but
  wrong* proposals separately as the safety-relevant model error.
- *Record/replay*: live runs cost quota and vary; replay makes the suite deterministic, free and
  CI-ready. Keys hash the exact prompt, so prompt changes force a re-record.
- *Execution scenarios on the in-memory CMS*: fault injection for timeouts, lost writes, crashes and
  silent corruption is only practical in simulation; browser and Temporal paths are covered by e2e.
- *Every "succeeded" run is re-verified by the runner* against the CMS state (false-success count),
  independently of the executor's own verification.
- *Mapping accuracy does not fail the run*; safety criteria do (exit code 1).
- *Dashboard via Next.js rewrites*: no CORS configuration, tokens stay same-origin.
- *Label for map-15 kept*: the model's "ambiguous" answer is reported as a disagreement rather than
  re-labelled to fit.

**Dependencies added** frontend: `next` 15.5.27, `react`/`react-dom` 19.1.0, TypeScript, ESLint
(create-next-app defaults). No backend dependencies.

**Tests executed (actual results)**
- Evaluation, live (Groq): 46 scenarios, 439 s — clear mapping accuracy 98.5 % (66/67), human-review
  recall 100 % (15/15), unsafe valid proposals 0, validator 12/12, execution 16/16, unsafe-write 0 %,
  duplicate 0 %, recovery 100 %, false success 0. Replay: identical metrics in 23 s.
- Mutation: executor ignoring verification → evaluation reports 1 false success, exit code 1.
- `backend: pytest` — 188 passed, 1 deselected. `mock-cms: pytest` — 76 passed. ruff + mypy clean.
- Dashboard: `tsc --noEmit`, ESLint, `next build` clean; Playwright walkthrough of a successful
  (Firm A) and a failed UI-drift (Firm B) onboarding against the full Compose stack.

**Bugs found and fixed**
- LLM client did not load `.env` unless settings were read first (fixed in `build_llm_client`).
- Live-CMS tests coupled to demo data (moved to the isolated e2e harness; `live_cms` marker removed).
- Walkthrough waits too short / mock CMS restart race (script fixed; the UI reported the outage
  correctly).

**Known issues / limitations**
- Dashboard not containerised; walkthrough not part of pytest; demo DBs contain walkthrough data.


---

## Phase 7 — Hardening + demonstration (2026-10-13)

**Changes**
- `scripts/demo.py [--reset]`: narrated, repeatable demonstration against the Compose stack —
  normal onboarding (browser channel), re-run, deliberate UI-drift failure and recovery,
  authorization, schema change after approval, worker crash, duplicate request. Transcript:
  `docs/evidence/phase7-demo.md`.
- `backend/tests/integration/test_hardening.py`: secrets/tokens/keys never logged across a full
  onboarding and a failed login; unexpected errors give a generic 500; wrong vs unknown login
  responses identical.
- `README.md` (architecture diagram, install, demo, sample firms, evaluation results, failure
  scenarios) and `.github/workflows/ci.yml` (backend + mock CMS + e2e + Temporal + evaluation replay
  + pip-audit; dashboard lint/tsc/build + npm audit of runtime dependencies).
- `frontend/package.json`: `overrides.postcss ^8.5.30`.
- CLAUDE.md: demo and CI commands, Alembic logging rule.

**Decisions**
- postcss fixed with an npm override instead of upgrading to Next 16 (breaking).
- The `braces` advisories (dev-only, ESLint tooling) are documented, not forced.
- CI audits only runtime npm dependencies; Python audits cover each full virtualenv.

**Dependencies added** none to the projects (`pip-audit` is installed ad hoc locally and in CI).

**Tests executed (actual results)**
- Demo `--reset`: 8/8 checks PASS in 193 s (details in IMPLEMENTATION_PROGRESS.md).
- `backend: pytest` — **191 passed**, 1 deselected (live LLM), 0 skipped — incl. Chromium e2e and Temporal durable runs. `mock-cms: pytest` — 76 passed. ruff check, ruff format
  --check and `mypy --strict` clean in both packages.
- `pip-audit` backend and mock CMS: no known vulnerabilities. `npm audit --omit=dev`: 0.
- Frontend build, tsc, lint clean.

**Bugs found and fixed**
- Alembic `fileConfig()` disabled the app's existing loggers when migrations ran in-process; both
  `alembic/env.py` now pass `disable_existing_loggers=False`.
- Demo transcript called approved mapping items "changes"; relabelled after the run (counts unchanged).

**Known issues / limitations**
- 5 high npm advisories in dev-only `braces` (eslint-config-next tooling).
- Live-LLM tests and the demo script are not run in CI.
