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
