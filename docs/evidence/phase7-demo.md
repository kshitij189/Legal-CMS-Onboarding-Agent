# Demonstration transcript

Run 2026-10-13T17:21:40+00:00 against the Compose stack (backend, Temporal worker, mock CMS). Synthetic data only.
Demo databases rebuilt from migrations and reseeded.

## 1. Normal onboarding — Firm A (browser channel)

- Source schema v1 uploaded (10 fields, json_schema).
- Target CMS inspected: 10 fields, CMS revision 0.
- 10 proposals from groq/openai/gpt-oss-120b; deterministic checks: {'valid': 10, 'needs_review': 0, 'invalid': 0}.
- Approved as immutable mapping version 1 (8 approved mapping items, fingerprint 655a261ffa50).
- Execute → HTTP 202 (accepted; runs on the Temporal worker).
- run 1 **succeeded** — steps {'applied': 8}, verification {'match': 8}, CMS revision 0 → 8
- Independent read of the CMS: revision 8, 8 intake mappings stored.

## 2. Re-run the same approved version

- run 2 **succeeded** — steps {'already_applied': 8}, verification {'match': 8}, CMS revision 8 → 8

## 3. Deliberate failure and safe recovery — Firm C, CMS UI changes

- Source schema v1 uploaded (10 fields, html_form).
- Target CMS inspected: 10 fields, CMS revision 0.
- 10 proposals from groq/openai/gpt-oss-120b; deterministic checks: {'valid': 7, 'needs_review': 3, 'invalid': 0}.
- Instruction-like text detected in ['notes']: treated as data, forced to human review.
  - `review_state` rejected by the reviewer (ambiguous — a person must decide).
- Approved as immutable mapping version 1 (9 approved mapping items, fingerprint 90c15724ea50).
- The CMS is switched to a new UI release (renamed form controls).
- run 3 **failed** — steps {'failed': 1, 'skipped': 9}, verification {'missing': 10}, CMS revision 0 → 0, reason `ui_drift`: Expected control 'Field name' was not found; stopping safely.
- CMS revision after the failed run: 0 (nothing was written).
- The CMS UI is restored; the same approved version is executed again.
- run 4 **succeeded** — steps {'applied': 10}, verification {'match': 10}, CMS revision 0 → 10

## 4. Authorization failures

- Firm B specialist opens Firm A's project → HTTP 404 (another firm's data is indistinguishable from missing).
- Integration specialist tries to create a project → HTTP 403 `missing_permission`.

## 5. Schema change after approval

- Firm A changes its form (email now required); executing the old approval → HTTP 409 `schema_changed`.

## 6. Worker crash during a run — Firm B

- Source schema v1 uploaded (10 fields, field_export_csv).
- Target CMS inspected: 10 fields, CMS revision 0.
- 10 proposals from groq/openai/gpt-oss-120b; deterministic checks: {'valid': 9, 'needs_review': 1, 'invalid': 0}.
- Approved as immutable mapping version 1 (9 approved mapping items, fingerprint a3088b1557ad).
- Worker container killed after 2 of 10 steps.
- While the worker is down: run `running`, steps {'applied': 2, 'pending': 8}.
- Worker restarted; Temporal hands the run back.
- run 5 **succeeded** — steps {'applied': 10}, verification {'match': 10}, CMS revision 0 → 10; attempts per step: [1].

## 7. Duplicate request

- Same Idempotency-Key twice → HTTP 202 then 200, run ids 6 and 6.

## Summary

| Check | Result |
|---|---|
| Normal onboarding verified | PASS |
| Re-run wrote nothing new | PASS |
| UI change stopped safely with zero writes | PASS |
| Same version recovered after the fix | PASS |
| Authorization enforced | PASS |
| Stale approval refused | PASS |
| Run survived a worker crash without repeats | PASS |
| Duplicate request returned the same run | PASS |

Completed in 193 s.
