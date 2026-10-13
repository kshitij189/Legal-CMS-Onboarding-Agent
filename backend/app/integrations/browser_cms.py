"""Playwright adapter that applies configuration through the mock CMS web UI (blueprint §10).

Rules it enforces:
- Controls are found only by accessible label or role + exact name. Before any interaction the
  control must exist exactly once; otherwise the run stops with `ui_drift` — it never falls back
  to a "similar" control, coordinates or fixed sleeps.
- Every browser request is checked against the connector's allowed origins; anything else is
  aborted and recorded.
- A Playwright trace and a screenshot are saved under the run's artifact folder when an action
  fails; successful runs keep no browser artifacts.
- Credentials are typed into the login form only; they are never logged or traced as text input
  (tracing records snapshots, not keystrokes).
"""

from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    Route,
    sync_playwright,
)
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from app.errors import ConnectorError
from app.integrations.base import ensure_allowed_origin, origin_of
from app.integrations.secrets import Credentials
from app.schemas.execution import CustomFieldSpec, MappingSpec, WriteOutcome

# The control names this adapter depends on (mock CMS UI "v1"). A renamed control is drift.
LABELS = {
    "username": "Username",
    "password": "Password",
    "sign_in": "Sign in",
    "entity": "Entity",
    "field_name": "Field name",
    "label": "Label",
    "data_type": "Data type",
    "required": "Required",
    "options": "Options (enum only, one per line)",
    "create_field": "Create field",
    "source_key": "Source field key",
    "target_field": "Target field",
    "transformation": "Transformation",
    "value_map": "Value map (enum targets only, one source=target per line)",
    "save_mapping": "Save mapping",
}


class BrowserCMSAdapter:
    channel = "browser"

    def __init__(
        self,
        base_url: str,
        credentials: Credentials,
        allowed_origins: frozenset[str],
        artifacts_dir: Path,
        timeout_ms: int = 5000,
        headless: bool = True,
    ) -> None:
        ensure_allowed_origin(base_url, allowed_origins)
        self._base_url = base_url.rstrip("/")
        self._allowed = {o.lower() for o in allowed_origins}
        self._credentials = credentials
        self._artifacts_dir = artifacts_dir
        self._timeout_ms = timeout_ms
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._failed = False
        self.blocked_requests: list[str] = []
        self.saved_artifacts: list[str] = []

    # --- lifecycle ------------------------------------------------------------------------------

    def __enter__(self) -> "BrowserCMSAdapter":
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        self._context = self._browser.new_context(base_url=self._base_url)
        self._context.route("**/*", self._guard)
        self._context.tracing.start(screenshots=True, snapshots=True)
        self._page = self._context.new_page()
        self._page.set_default_timeout(self._timeout_ms)
        try:
            self._login()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if self._context is not None:
                if self._failed or exc is not None:
                    self._save_failure_artifacts()
                else:
                    self._context.tracing.stop()
                self._context.close()
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()
            self._playwright = self._browser = self._context = self._page = None

    def _save_failure_artifacts(self) -> None:
        assert self._context is not None  # noqa: S101
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        if self._page is not None:
            shot = self._artifacts_dir / "failure.png"
            try:
                self._page.screenshot(path=str(shot), full_page=True)
                self.saved_artifacts.append(shot.name)
            except PlaywrightError:
                pass
        trace = self._artifacts_dir / "trace.zip"
        self._context.tracing.stop(path=str(trace))
        self.saved_artifacts.append(trace.name)

    def _guard(self, route: Route) -> None:
        """Allow only requests to allowed origins. Redirects are not re-routed by the browser, so
        each allowed request is fetched here without following redirects and any redirect that
        points elsewhere is refused before the browser can follow it."""
        url = route.request.url
        if origin_of(url) not in self._allowed:
            self.blocked_requests.append(url)
            route.abort("blockedbyclient")
            return
        response = route.fetch(max_redirects=0)
        location = response.headers.get("location")
        if 300 <= response.status < 400 and location:
            target = urljoin(url, location)
            if origin_of(target) not in self._allowed:
                self.blocked_requests.append(target)
                route.abort("blockedbyclient")
                return
        route.fulfill(response=response)

    # --- helpers --------------------------------------------------------------------------------

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("BrowserCMSAdapter used outside its context manager")
        return self._page

    def _fail(self, category: str, message: str, details: dict[str, Any]) -> ConnectorError:
        self._failed = True
        return ConnectorError(category, message, [{**details, "url": self.page.url}])

    def _control(self, locator: Locator, name: str) -> Locator:
        """Return the single matching control, or stop: never act on a guess."""
        try:
            locator.first.wait_for(state="visible", timeout=self._timeout_ms)
        except PlaywrightTimeout:
            raise self._fail(
                "ui_drift",
                f"Expected control '{name}' was not found; stopping safely.",
                {"code": "control_missing", "control": name},
            ) from None
        count = locator.count()
        if count != 1:
            raise self._fail(
                "ui_drift",
                f"Control '{name}' is ambiguous ({count} matches); stopping safely.",
                {"code": "control_ambiguous", "control": name, "matches": count},
            )
        return locator

    def _by_label(self, key: str) -> Locator:
        return self._control(self.page.get_by_label(LABELS[key], exact=True), LABELS[key])

    def _button(self, key: str) -> Locator:
        return self._control(
            self.page.get_by_role("button", name=LABELS[key], exact=True), LABELS[key]
        )

    def _check_origin(self) -> None:
        """Redirects are followed by the browser without passing the request guard, so the page's
        final origin is checked after every navigation."""
        if origin_of(self.page.url) not in self._allowed:
            raise self._fail(
                "security",
                "Navigation left the allowed CMS origins; stopping.",
                {"code": "origin_left", "origin": origin_of(self.page.url)},
            )

    def _goto(self, path: str) -> None:
        try:
            response = self.page.goto(path)
        except PlaywrightError as exc:
            if self.blocked_requests:
                raise self._fail(
                    "security",
                    "Navigation to a non-allowed origin was blocked.",
                    {"code": "navigation_blocked", "blocked": self.blocked_requests[-1]},
                ) from None
            raise self._fail(
                "transient", f"Page did not load ({type(exc).__name__}).", {"code": "page_load"}
            ) from None
        self._check_origin()
        if response is not None and response.status == 403:
            raise self._fail(
                "authorization",
                "The CMS account may not open this page.",
                {"code": "forbidden_page", "path": path},
            )
        if response is not None and response.status >= 500:
            raise self._fail("transient", "The CMS page failed to load.", {"code": "page_error"})

    def _select(self, key: str, value: str) -> None:
        control = self._by_label(key)
        if control.locator(f'option[value="{value}"]').count() != 1:
            raise self._fail(
                "validation",
                f"'{value}' is not offered in '{LABELS[key]}'.",
                {"code": "option_missing", "control": LABELS[key], "value": value},
            )
        control.select_option(value=value)

    def _submit(self, key: str) -> WriteOutcome:
        """Click and wait for the CMS's own success notice or error alert."""
        self._button(key).click()
        outcome = self.page.get_by_role("status").or_(self.page.get_by_role("alert")).first
        try:
            outcome.wait_for(state="visible", timeout=self._timeout_ms)
        except PlaywrightTimeout:
            # Nothing confirms or denies the save: the caller must read state before retrying.
            raise self._fail(
                "uncertain", "No confirmation after submitting.", {"code": "no_feedback"}
            ) from None
        self._check_origin()
        alert = self.page.get_by_role("alert")
        if alert.count() > 0:
            raise self._fail(
                "validation",
                "The CMS rejected the form.",
                {"code": "form_error", "messages": alert.first.inner_text().splitlines()},
            )
        notice = self.page.get_by_role("status").first.inner_text().strip()
        if notice.startswith("Saved."):
            changed: bool | None = True
        elif notice.startswith("No changes"):
            changed = False
        else:
            raise self._fail("contract", "Unrecognised CMS notice.", {"notice": notice})
        return WriteOutcome(changed=changed, evidence={"channel": "browser", "notice": notice})

    # --- operations -----------------------------------------------------------------------------

    def _login(self) -> None:
        self._goto("/login")
        self._by_label("username").fill(self._credentials.username)
        self._by_label("password").fill(self._credentials.password)
        # Wait for the navigation the click triggers (waiting for "load" afterwards races it).
        with self.page.expect_navigation():
            self._button("sign_in").click()
        self._check_origin()
        if self.page.url.rstrip("/").endswith("/login"):
            raise self._fail(
                "authorization",
                "The CMS rejected the connector's credentials.",
                {"code": "login_failed"},
            )

    def create_custom_field(self, spec: CustomFieldSpec) -> WriteOutcome:
        self._goto("/config/custom-fields/new")
        self._select("entity", spec.entity)
        self._by_label("field_name").fill(spec.name)
        self._by_label("label").fill(spec.label)
        self._select("data_type", spec.data_type)
        self._by_label("required").set_checked(spec.required)
        self._by_label("options").fill("\n".join(spec.options or []))
        return self._submit("create_field")

    def upsert_mapping(self, spec: MappingSpec) -> WriteOutcome:
        self._goto("/config/mappings")
        self._by_label("source_key").fill(spec.source_key)
        self._select("target_field", spec.target_field)
        self._select("transformation", spec.transformation)
        self._by_label("value_map").fill(
            "\n".join(f"{k}={v}" for k, v in (spec.value_map or {}).items())
        )
        return self._submit("save_mapping")
