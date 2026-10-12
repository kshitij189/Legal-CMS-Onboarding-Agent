# Legal CMS Onboarding & Integration Agent
## Complete Project Blueprint

> Source of truth for requirements, architecture, technology choices, phases and acceptance criteria.
> Committed verbatim from the project owner's brief (decision D2, 2026-10-12). Where the text says
> "Claude", read "the LLM" — the approved provider is Gemini, with Groq as optional fallback (decision D1).

## 1. Product vision

**Product name:** Legal CMS Onboarding & Integration Agent

**Category:** Domain-specific browser agent and integration automation platform.

**Primary goal:** Help law firms onboard their existing intake forms and workflows into a case management system (CMS) by automatically mapping fields, configuring supported integrations, validating the resulting configuration, and documenting anything that requires human intervention.

The initial product will run against a simulated law firm and a locally developed CMS. It will use the gemini and groq API for reasoning, Playwright for browser automation, and a local application stack for data storage, workflow execution, and evaluation.

The architecture should eventually support adapters for different legal practice management systems without hard-coding one firm's workflow into the agent.

---

## 2. The real-world problem

Different law firms collect different information and use different terminology.

One firm may collect a prospective client's mobile number under `client_mobile`. Another may call the same field `contact_phone`. A third may use `phone_number` but require a separate country-code field.

The firms may also have different required fields, matter categories, custom fields, workflow statuses, document requirements, and access permissions.

When a firm adopts or integrates a new case management system, someone may have to inspect the existing forms, understand the field definitions, map the old schema to the new one, configure the system, and verify that the resulting records are correct.

This is the workflow your agent will automate.

### Example

Suppose a fictional law firm uses this intake form:

- Client full name
- Mobile number
- Email address
- Type of legal matter
- Opposing party
- Jurisdiction
- Preferred callback time

The target CMS uses a different schema:

- `contact.full_name`
- `contact.phone_number`
- `contact.email`
- `matter.practice_area`
- `matter.opposing_party`
- `matter.jurisdiction`
- `contact.callback_preference`

Your agent should identify these correspondences, determine whether transformations are needed, flag ambiguous mappings, and prepare a valid configuration for approval.

**The goal is not to make the agent guess every field. The goal is to automate the mappings it can justify and surface the ones that need a human decision.**

---

## 3. Who will use this product?

The initial target customer is a small or midsize law firm onboarding a new CMS or integrating an existing intake process.

The users are:

1. **Firm administrator:** Configures the firm's intake fields and workflow requirements.
2. **Integration specialist:** Reviews proposed mappings and approves configuration changes.
3. **Intake coordinator:** Uses the configured system to manage prospective client enquiries.
4. **Law firm staff:** Reviews the resulting records and exceptions.

For the personal project, these users will be simulated through local accounts and a dashboard.

You do not need a real law firm, a commercial CMS subscription, or production customer data to develop the first version.

---

## 4. What the agent will do

The MVP should support six capabilities.

### Capability 1 — Discover the source schema

The agent receives a firm's sample intake form, a JSON schema, or a field-definition export.

It identifies the available fields, labels, data types, descriptions, and required-field rules.

When the information is available through a documented API, use that API instead of visually inspecting every page.

### Capability 2 — Inspect the target CMS

The agent discovers the destination CMS's supported entities, fields, required values, and configuration options.

The first version will use a CMS that you develop yourself. You can later add adapters for real systems with authorized test access.

### Capability 3 — Generate field mappings

Gemini/Groq analyzes the source and target definitions and proposes mappings.

For example:

`client_mobile` → `contact.phone_number`

Each proposed mapping should include:

- Source field and target field.
- Expected data types.
- Transformation, if necessary.
- Whether the field is required.
- Confidence or ambiguity indicators.
- An explanation based on the available definitions.
- Any unresolved questions.

The confidence score must not be treated as proof of correctness. Important mappings need validation against the schema and, where appropriate, human review.

### Capability 4 — Request human approval

The dashboard shows the proposed mappings before configuration changes occur.

The reviewer can approve, reject, edit, or request clarification for individual mappings.

The agent should not autonomously change a live firm's configuration simply because the model believes its proposal is correct.

### Capability 5 — Execute the approved configuration

After approval, the agent uses the relevant CMS adapter to apply the mappings.

Prefer documented APIs when they support the required operation. Use Playwright for UI-only operations or workflows that lack a suitable API.

Actions should be typed, validated, auditable, and restricted to the approved plan.

### Capability 6 — Verify and report

After making changes, the agent reads the saved configuration back from the CMS and compares it with the approved mapping.

The report should identify:

- Successfully configured mappings.
- Failed configuration steps.
- Missing required fields.
- Unsupported data types.
- Unresolved ambiguities.
- Changes that need manual intervention.
- Evidence supporting the final status.

The agent must not claim successful onboarding simply because it clicked a Save button.

---

## 5. Full end-to-end workflow

The application should follow this sequence:

1. A firm administrator creates an onboarding project.
2. The source form or schema is uploaded or retrieved through an authorized integration.
3. The agent inspects the target CMS and discovers its supported fields.
4. Both schemas are normalized into a common representation.
5. Gemini/Groq proposes field mappings and identifies ambiguities.
6. The application validates the proposed mappings against deterministic rules.
7. An authorized human reviews and approves the configuration plan.
8. The integration adapter executes the approved changes.
9. The agent reads the resulting configuration back from the CMS.
10. The verification engine compares the actual configuration with the approved plan.
11. The system saves the final onboarding report, audit events, and any unresolved exceptions.

A failed step should not silently be treated as successful. The workflow must record where it stopped and whether a retry or human intervention is required.

---

## 6. Example of a field-mapping record

A proposed mapping could look like this:

```json
{
  "source_field": "client_mobile",
  "target_field": "contact.phone_number",
  "source_type": "string",
  "target_type": "string",
  "transformation": "normalize_phone",
  "required": true,
  "status": "pending_approval",
  "reason": "The source field represents a client's mobile number, matching the target contact phone field.",
  "validation_requirements": [
    "Target field exists",
    "Source and target types are compatible",
    "Transformation is supported",
    "Required-field rule is satisfied"
  ]
}
```

This is an example schema, not an output that the agent should accept without checking.

The application should reject mappings to nonexistent fields, unsupported transformations, unauthorized configuration areas, or incompatible data types.

The model proposes the mapping; deterministic application code validates it.

---

## 7. The canonical legal data model

A major engineering decision is to avoid writing separate mapping logic for every possible source-target pair.

Instead, introduce a canonical model that represents the concepts your initial product supports.

The initial entities can be:

### Client
- Client identifier
- Full name
- Email
- Phone number
- Preferred contact method

### Matter
- Matter identifier
- Matter category
- Jurisdiction
- Opposing party
- Summary
- Intake status

### Intake
- Intake identifier
- Source form
- Collected values
- Missing fields
- Validation status
- Review status

### Task
- Task identifier
- Task type
- Assigned role or user
- Due date
- Status

### Document reference
- Document identifier
- Document type
- Storage reference
- Associated intake or matter

These are integration concepts, not a complete legal case-management schema.

Each vendor-specific adapter translates between its schema and the canonical model. Keep vendor-specific fields in a controlled extension structure rather than discarding them.

For example:

Source schema → Canonical model → Target CMS adapter

This is much easier to extend than implementing a separate mapping engine for every pair of platforms.

---

## 8. Final tech stack

All of the following can be used for a locally hosted personal project, except that Gemini/Groq API usage is subject to your existing account's usage limits and billing.

| Layer | Technology | Responsibility |
|---|---|---|
| Programming language | Python | Agent orchestration and integration logic |
| LLM | gemini with the google Python SDK | Schema interpretation, mapping proposals and action selection |
| Browser automation | Playwright for Python | Interacting with authorized CMS interfaces |
| Backend | FastAPI | APIs, integrations and configuration management |
| Validation | Pydantic + JSON Schema | Validate mappings, tool inputs, and configuration plans |
| Database | PostgreSQL | Firms, schemas, configurations, runs, approvals and audit records |
| ORM and migrations | SQLAlchemy + Alembic | Database persistence and schema evolution |
| Durable workflows | Self-hosted Temporal | Multi-step onboarding, retries and recovery |
| Frontend | Next.js + TypeScript | Mapping review and onboarding dashboard |
| Artifact storage | Local filesystem in development | Sample forms, screenshots and onboarding reports |
| Testing | pytest + Playwright | Unit, integration and browser tests |
| Observability | OpenTelemetry + structured logging | Runtime traces, errors and latency |
| Agent tracing | Self-hosted Langfuse, optional | Inspect Claude requests, tool calls and evaluation runs |
| Containers | Docker + Docker Compose | Reproducible local development |
| CI/CD | GitHub Actions | Automated tests and regression checks |

You do not need a separate commercial browser-agent framework. Playwright is your browser executor, and gemini/groq supplies the reasoning.

You also do not need a vector database initially. Your core problem is schema mapping and workflow execution, not semantic search over a large document collection.

---

## 9. Architecture

Separate the application into the reasoning layer, integration layer, execution layer, and verification layer.

### Main components

**Frontend**
- Onboarding project creation.
- Source and target schema views.
- Mapping approval interface.
- Workflow status and execution logs.
- Verification report.

**FastAPI backend**
- Project and schema APIs.
- Mapping proposal endpoints.
- Approval endpoints.
- Onboarding workflow initiation.
- Audit and verification APIs.

**Agent service**
- Claude client.
- Prompt and schema-mapping logic.
- Typed tool definitions.
- Mapping proposal generation.
- Execution-plan generation.

**Integration adapters**
- Common adapter interface.
- Local mock CMS adapter.
- API-based CMS connector.
- Playwright-based UI connector where needed.

**Execution worker**
- Executes approved operations.
- Validates action permissions.
- Manages browser sessions.
- Captures results.
- Recovers or stops safely when execution fails.

**Verification engine**
- Re-reads the saved configuration.
- Compares expected and actual values.
- Records discrepancies.
- Generates the final report.

**PostgreSQL**
- The authoritative source for application state, mapping versions, approvals, workflow records, and audit history.

**Temporal**
- Orchestrates long-running onboarding workflows that must survive timeouts and worker restarts.

### High-level flow

Source schemas → Schema normalization → Claude mapping proposal → Validation → Human approval → Approved execution plan → CMS adapter or Playwright → Read-after-write verification → Audit report.

Do not let Claude run arbitrary SQL, shell commands, or unrestricted browser actions. All operations should pass through a typed, permission-checked application interface.

---

## 10. How to design the browser agent

Playwright should be responsible for deterministic browser interactions. Claude should make semantic decisions only where the workflow genuinely requires reasoning.

For example, a tool interface could expose these operations:

- `inspect_target_form`
- `get_supported_fields`
- `propose_field_mapping`
- `validate_mapping`
- `preview_configuration_change`
- `apply_approved_mapping`
- `read_saved_configuration`
- `compare_expected_and_actual`
- `generate_onboarding_report`

Each tool should have a defined input and output schema.

The execution process should work like this:

1. Claude proposes an action through a structured tool call.
2. Pydantic validates its arguments.
3. The authorization layer checks that the action is allowed for that firm and onboarding run.
4. The adapter or Playwright worker executes it.
5. The application records the result.
6. The verification layer checks the resulting system state.
7. Claude receives the verified result if further reasoning is required.

Use stable Playwright locators based on roles, labels, and accessible names wherever possible. Avoid fixed sleeps and screen coordinates.

If a page layout changes, the agent should detect that an expected element or field is missing and stop safely instead of clicking a possibly unrelated control.

Keep the browser worker isolated, restrict it to authorized domains, and never expose credentials or unrestricted computer access to the model.

---

## 11. Database design

For the initial implementation, PostgreSQL can store the following entities.

### `firms`
Stores the firm identity, configuration, timezone and active status.

### `cms_connectors`
Stores the connector type, configuration reference, supported operations and authorization metadata. Secrets should be stored separately from ordinary application records.

### `onboarding_projects`
Represents an onboarding task for a specific firm and destination CMS.

### `schema_definitions`
Stores versioned source and target schema representations.

### `field_definitions`
Stores normalized field names, labels, descriptions, types, required constraints and supported values.

### `mapping_proposals`
Stores candidate source-to-target mappings, transformations, rationale, validation results and confidence information.

### `mapping_versions`
Stores approved mapping versions so changes can be compared and rolled back when appropriate.

### `onboarding_runs`
Tracks each execution, its status, timestamps, configuration version and final result.

### `action_steps`
Stores the planned and executed steps, including tool inputs, outcomes and error classifications. Avoid storing unnecessary sensitive payloads.

### `verification_results`
Stores expected versus actual values, mismatch reasons, evidence references and verification status.

### `audit_events`
Records who initiated, approved, executed or rejected a change, together with the associated entity and timestamp.

Use foreign keys, tenant-scoped access checks and database constraints to maintain integrity.

Important rule: approved mapping versions and actual execution results must remain distinguishable. A proposal is not an approved configuration, and an executed action is not necessarily a verified success.

---

## 12. API design

A possible FastAPI surface is:

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/onboarding-projects` | Create an onboarding project |
| `GET /api/v1/onboarding-projects/{id}` | Retrieve its current status |
| `POST /api/v1/onboarding-projects/{id}/source-schema` | Upload or register a source schema |
| `POST /api/v1/onboarding-projects/{id}/inspect-target` | Inspect the target CMS |
| `POST /api/v1/onboarding-projects/{id}/propose-mappings` | Generate mapping proposals |
| `GET /api/v1/onboarding-projects/{id}/mappings` | Retrieve proposed mappings |
| `POST /api/v1/onboarding-projects/{id}/approve-mappings` | Approve selected mapping changes |
| `POST /api/v1/onboarding-projects/{id}/execute` | Execute an approved plan |
| `GET /api/v1/onboarding-projects/{id}/verification` | Retrieve verification results |
| `GET /api/v1/onboarding-projects/{id}/audit-events` | Inspect the action history |

The endpoint definitions are a proposed API design. Implement authorization, structured errors, and idempotency for write operations.

The frontend must not be able to bypass the approval stage or execute arbitrary browser actions.

---

## 13. How to test it without real customers

Build a local test environment consisting of a mock legal CMS, fictional firms, sample intake forms, and a ground-truth mapping dataset.

Use three fictional firm configurations:

**Firm A**
- Requires client name, phone number, and matter category.

**Firm B**
- Requires client name, email, jurisdiction, and opposing party.

**Firm C**
- Uses custom field names, a different matter-category vocabulary, and a required review status.

The CMS should support login, configurable fields, form submission, persisted configuration, validation errors, and controlled UI changes.

This is more useful than testing against static HTML because the target system has actual state to verify.

### Build the initial evaluation dataset

Start with approximately 30–50 labelled scenarios.

| Scenario | Expected result |
|---|---|
| Clear one-to-one mapping | Correct proposal and verified configuration |
| Different field names, same meaning | Correct mapping with supporting evidence |
| Ambiguous target field | Ask for clarification or leave unresolved |
| Incompatible source and target types | Reject the mapping or require an explicit supported transformation |
| Missing required source field | Mark onboarding incomplete |
| Unexpected CMS field | Preserve it or flag it for review |
| UI element renamed | Detect the change and stop safely |
| Save request times out after succeeding | Read actual state before retrying |
| Repeated execution request | Prevent duplicate or repeated unintended changes |
| Unauthorized configuration operation | Reject the operation |
| Firm's schema changes after approval | Detect the version mismatch and require revalidation |
| Untrusted text contains instructions to ignore policy | Treat it as data, not as permission to override the workflow |

### What should you measure?

**Mapping accuracy:** Percentage of mappings that match the labelled ground truth.

**Required-field coverage:** Whether every mandatory target field is mapped or explicitly marked unresolved.

**Verification success:** Whether the final persisted configuration matches the approved plan.

**Unsafe-write rate:** Whether the system ever executes a change without the required authorization or approval.

**Duplicate execution rate:** Whether retries create repeated side effects.

**Recovery success:** Whether a workflow can resume after a timeout or worker restart.

**Human-review rate:** How often the agent correctly identifies ambiguity or unsupported requirements.

For the first acceptance suite, aim for zero unauthorized writes and zero false success reports. Set a provisional target of at least 95% correct mapping decisions on your curated, clearly mappable cases, while requiring ambiguous and incompatible cases to be safely rejected. Report the actual results; do not present results on synthetic data as proof of accuracy with real law firms.

When the agent fails, save the case and add it to the regression suite.

---

## 14. Security and production reliability

Even though this is initially a personal project, include the controls that matter in a real deployment.

### Human approval
Require authorization before applying live configuration changes. The approval should refer to a specific mapping version so that the approved plan cannot silently change afterward.

### Least privilege
Give each connector only the permissions required for its supported operations. Do not use an unrestricted administrator account for routine onboarding.

### Tenant isolation
Scope records and connector access by firm. One firm's schemas, configurations, or credentials must not leak into another firm's onboarding workflow.

### Prompt-injection resistance
Treat the contents of web pages, uploaded forms, labels and descriptions as untrusted data. Never allow instructions found in those sources to override application policy or authorize new actions.

### Idempotency
Use unique execution IDs and idempotency keys for actions that may be retried. Check the persisted result before repeating an operation whose previous outcome is uncertain.

### Audit history
Record mapping proposals, approvals, execution outcomes, failures and verification results. Redact secrets and unnecessary personal information from logs.

### Recovery
Persist workflow state, distinguish transient failures from permanent validation failures, and use bounded retries. A failed or uncertain action must not be reported as successful.

### Safe execution
Restrict the browser worker to permitted domains and operations. Keep credentials outside model prompts and avoid unrestricted remote debugging or browser control endpoints.

---

## 15. Repository structure

A sensible initial structure would be:

```text
legal-cms-onboarding/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── onboarding.py
│   │   │   ├── schemas.py
│   │   │   ├── mappings.py
│   │   │   └── verification.py
│   │   ├── agent/
│   │   │   ├── claude_client.py
│   │   │   ├── prompts.py
│   │   │   ├── mapping_agent.py
│   │   │   └── tool_registry.py
│   │   ├── domain/
│   │   │   ├── schema_normalizer.py
│   │   │   ├── mapping_validator.py
│   │   │   ├── execution_service.py
│   │   │   └── verification_service.py
│   │   ├── integrations/
│   │   │   ├── base.py
│   │   │   ├── mock_cms.py
│   │   │   ├── api_cms.py
│   │   │   └── browser_cms.py
│   │   ├── workflows/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── db/
│   │   └── observability/
│   └── tests/
│       ├── unit/
│       ├── integration/
│       ├── browser/
│       └── evaluation/
├── frontend/
│   ├── app/
│   ├── components/
│   └── lib/
├── mock-cms/
│   ├── app/
│   ├── fixtures/
│   └── tests/
├── fixtures/
│   ├── source-schemas/
│   ├── target-schemas/
│   └── expected-mappings/
├── scripts/
│   ├── seed_demo_data.py
│   └── run_evaluations.py
├── docker-compose.yml
├── .env.example
└── README.md
```

This is an initial design. Keep the application as a modular monolith with dedicated execution workers until there is a real reason to split services further.

---

## 16. Implementation roadmap

### Phase 1 — Build the mock CMS
Create the local legal case-management application with login, configurable fields, role-based permissions, and persisted configuration.

**Exit criterion:** You can configure the CMS manually and verify saved values through an API or database query.

### Phase 2 — Build schema normalization
Create Pydantic models for source fields, target fields, canonical entities, mappings and transformations.

**Exit criterion:** The application can normalize multiple fictional firm's schemas into consistent representations.

### Phase 3 — Integrate Claude
Implement mapping proposals with structured tool calls and validation. Keep approval and execution separate from proposal generation.

**Exit criterion:** Claude produces candidate mappings that your deterministic validator can accept, reject or mark ambiguous.

### Phase 4 — Add Playwright execution
Implement browser navigation and a controlled set of UI actions. Add state assertions, error handling and screenshots or traces for failed cases.

**Exit criterion:** The agent can execute an approved configuration in the mock CMS and read the result back.

### Phase 5 — Implement durable orchestration
Use Temporal for onboarding runs, retries, approvals, and recovery.

**Exit criterion:** Interrupted work resumes from persisted state without repeating completed side effects.

### Phase 6 — Build evaluation and the dashboard
Implement the scenario runner, expected-versus-actual verification, mapping review interface and audit history.

**Exit criterion:** One command runs the evaluation suite, and the dashboard explains successes and failures.

### Phase 7 — Harden and demonstrate
Test unauthorized actions, UI changes, timeouts, duplicate requests, schema changes and untrusted input.

**Exit criterion:** The project provides a repeatable demonstration that includes a normal onboarding and at least one deliberate failure with safe recovery.

---

## 17. What makes this a strong FDE portfolio project?

The product demonstrates much more than browser automation.

It shows that you can:

- Discover and normalize customer-specific schemas.
- Build reusable vendor integration adapters.
- Use an LLM for semantic mapping without delegating all correctness to the model.
- Translate customer requirements into a configuration plan.
- Execute changes through APIs and controlled browser actions.
- Build human-approval workflows for risky operations.
- Verify that real persisted state matches the intended configuration.
- Handle schema drift, permission failures, timeouts and retries.
- Produce measurable evaluation evidence and an auditable execution history.

This is directly relevant to FDE work because real deployments are rarely clean, uniform environments. Each customer has different fields, access permissions and operational assumptions.

The differentiator is your ability to make the agent adapt to a new customer's workflow without losing control over the resulting system changes.

---

## 18. Definition of done

The first release is complete when you can:

1. Create a fictional law firm in the dashboard.
2. Upload its sample intake schema.
3. Inspect the target CMS.
4. Generate and review field mappings.
5. Approve the mappings.
6. Execute the configuration through the actual mock integration.
7. Read back the saved configuration and verify it.
8. Inspect a detailed report of the successful and failed actions.
9. Re-run the workflow without creating unintended duplicates.
10. Demonstrate that an ambiguous mapping, authorization failure, or CMS UI change is handled safely.

Your README should include the architecture diagram, installation instructions, sample firm configurations, evaluation dataset description, results, failure scenarios, and a short video or reproducible walkthrough.

**The final objective:** Build a browser agent that can onboard a new firm's configuration in a repeatable, verifiable way—not one that simply fills out a form. That is a much stronger demonstration of production-oriented AI engineering.

The implementation order I would follow is mock CMS → schema normalization → Claude mapping proposals → approval workflow → Playwright execution → verification and evaluation. That order gives you a working, testable product at each stage and avoids building a browser agent before you have defined what a correct outcome looks like.
