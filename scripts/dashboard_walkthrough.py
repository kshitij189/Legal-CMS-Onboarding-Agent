"""Reproducible dashboard walkthrough (Phase 6 evidence) against the running Compose stack.

Prerequisites: `docker compose up -d` (backend, worker, temporal, mock-cms seeded) and the dashboard
on http://localhost:3000 (`cd frontend; npm run build; npx next start -p 3000`). Uses the real LLM
provider configured for the backend (one proposal request per journey).

    backend\\.venv\\Scripts\\python scripts\\dashboard_walkthrough.py

Journey 1 (Firm A): sign in, create a project, upload the schema, inspect the CMS, propose,
review every proposal, approve a version, execute through the browser channel, show the verified
run and the audit log. Journey 2 (Firm B): the mock CMS is switched to its renamed-controls UI
(v2) to show how a failed run is explained, then restored to v1.
Screenshots go to docs/evidence/phase6-dashboard/.
"""

import os
import re
import subprocess
import time
from pathlib import Path

import httpx
from playwright.sync_api import Page, expect, sync_playwright

REPO = Path(__file__).resolve().parents[1]
BASE = os.environ.get("DASHBOARD_URL", "http://localhost:3000")
OUT = REPO / "docs" / "evidence" / "phase6-dashboard"
FIXTURES = REPO / "fixtures" / "source-schemas"


def shot(page: Page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
    print(f"  screenshot {name}.png")


def compose(*args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(["docker", "compose", *args], cwd=REPO, check=True, capture_output=True,
                   env={**os.environ, **(env or {})})


def restart_mock_cms(variant: str) -> None:
    """Recreate the mock CMS with a UI variant and wait until it answers again."""
    compose("up", "-d", "mock-cms", env={"MOCK_CMS_UI_VARIANT": variant})
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if httpx.get("http://localhost:8100/healthz", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError("mock CMS did not come back")


def sign_in(page: Page, username: str) -> None:
    page.goto(BASE)
    page.get_by_label("Username").fill(username)
    page.get_by_label("Password").fill("demo-password")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.get_by_role("heading", name=re.compile("Onboarding projects"))).to_be_visible()


def onboard(page: Page, prefix: str, name: str, fmt: str, fixture: str) -> None:
    page.get_by_label("Project name").fill(name)
    page.get_by_role("button", name="Create project").click()
    expect(page.get_by_role("heading", name=name)).to_be_visible()
    page.get_by_label("Format").select_option(fmt)
    page.get_by_label("File").set_input_files(str(FIXTURES / fixture))
    page.get_by_role("button", name="Upload source schema").click()
    expect(page.get_by_role("status")).to_contain_text("registered")
    page.get_by_role("button", name="Inspect target CMS").click()
    expect(page.get_by_role("status")).to_contain_text(re.compile("captured|unchanged"))
    shot(page, f"{prefix}-1-overview")
    page.get_by_role("tab", name="Schemas").click()
    expect(page.get_by_role("table", name="Target fields")).to_be_visible()
    shot(page, f"{prefix}-2-schemas")
    page.get_by_role("tab", name="Overview").click()
    page.get_by_role("button", name="Propose mappings").click()
    proposals = page.get_by_role("table", name="Mapping proposals")
    expect(proposals).to_be_visible(timeout=180_000)
    shot(page, f"{prefix}-3-proposals")

    fields = [row.get_attribute("data-testid") or "" for row in proposals.locator("tbody tr").all()]
    for testid in fields:
        field = testid.removeprefix("proposal-")
        row = page.get_by_test_id(testid)
        text = row.inner_text()
        if "ambiguous" in text or re.search(r"\binvalid\b", text):
            action = "Reject"
        else:
            if "needs review" in text:
                row.get_by_label(f"Comment for {field}").fill("Checked against the firm's form.")
            action = "Approve"
        row.get_by_role("button", name=f"{action} {field}").click()
        expect(page.get_by_test_id(testid)).to_contain_text(
            "approved" if action == "Approve" else "rejected")
    shot(page, f"{prefix}-4-reviewed")
    page.get_by_role("button", name="Approve mapping version").click()
    expect(page.get_by_role("button", name=re.compile(r"Execute version \d+"))).to_be_visible()
    shot(page, f"{prefix}-5-version")
    page.get_by_label("Apply through").select_option("browser")
    page.get_by_role("button", name=re.compile(r"Execute version \d+")).click()


def wait_for_run(page: Page, final: str) -> None:
    status = page.get_by_role("status").first
    expect(status).not_to_contain_text("running", timeout=240_000)
    expect(status).to_contain_text(final)


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        # Dynamic pages render on demand; allow for slow first renders without fixed sleeps.
        page.set_default_timeout(20_000)
        expect.set_options(timeout=20_000)

        print("Journey 1: Firm A, successful onboarding")
        sign_in(page, "a.admin")
        shot(page, "a-0-projects")
        onboard(page, "a", "Alder dashboard walkthrough", "json_schema", "firm-a.schema.json")
        wait_for_run(page, "succeeded")
        shot(page, "a-6-run-succeeded")
        page.get_by_role("tab", name="Audit log").click()
        expect(page.get_by_role("table", name="Audit events")).to_contain_text("run.succeeded")
        shot(page, "a-7-audit")

        print("Journey 2: Firm B, CMS UI changed (renamed controls)")
        restart_mock_cms("v2")
        try:
            page.get_by_role("button", name="Sign out").click()
            sign_in(page, "b.admin")
            onboard(page, "b", "Birch dashboard walkthrough", "field_export_csv",
                    "firm-b.fields.csv")
            wait_for_run(page, "failed")
            expect(page.get_by_role("status").first).to_contain_text("stopped instead of guessing")
            shot(page, "b-6-run-failed-ui-drift")
        finally:
            restart_mock_cms("v1")
        browser.close()
    print(f"Done. Screenshots in {OUT}")


if __name__ == "__main__":
    main()
