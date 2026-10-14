"""Provider-neutral LLM client (decision D1, revised 2026-10-13): Groq primary, Gemini fallback.

The only capability exposed is a *forced structured tool call*: the model must answer by calling one
declared tool whose arguments are a Pydantic model. Free text is never accepted as output, and the
arguments are validated before anything else sees them. API keys are read from the environment and
never logged or placed in prompts.
"""

import json
import logging
import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ValidationError

from app.agent.tool_registry import GEMINI_UNSUPPORTED_KEYWORDS, ToolSpec, inline_json_schema
from app.config import get_settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    """category: configuration | quota | transient | invalid_output | provider"""

    def __init__(self, category: str, message: str, provider: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.provider = provider


@dataclass(frozen=True)
class ToolCallResult:
    arguments: BaseModel
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    # Providers tried before this one succeeded, with the error category of each.
    fallbacks: tuple[tuple[str, str], ...] = ()


class LLMClient(Protocol):
    provider: str
    model: str

    def call_tool(self, system: str, user: str, tool: ToolSpec) -> ToolCallResult: ...


def _parse_arguments(tool: ToolSpec, raw: Any, provider: str) -> BaseModel:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return tool.args_model.model_validate(data)
    except (ValueError, ValidationError) as exc:
        raise LLMError(
            "invalid_output", f"Tool arguments failed validation: {str(exc)[:300]}", provider
        ) from None


class GeminiClient:
    provider = "gemini"

    def __init__(
        self, api_key: str, model: str, timeout_seconds: float, sdk_client: Any = None
    ) -> None:
        from google import genai
        from google.genai import types

        self.model = model
        self._types = types
        # Bounded SDK retries: a quota or overload error must fail fast, not stall the request.
        self._client = sdk_client or genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout_seconds * 1000),
                retry_options=types.HttpRetryOptions(attempts=2, initial_delay=1.0),
            ),
        )

    def call_tool(self, system: str, user: str, tool: ToolSpec) -> ToolCallResult:
        types = self._types
        started = time.perf_counter()
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=0,
                    tools=[
                        types.Tool(
                            function_declarations=[
                                types.FunctionDeclaration(
                                    name=tool.name,
                                    description=tool.description,
                                    parameters_json_schema=inline_json_schema(
                                        tool.args_model, GEMINI_UNSUPPORTED_KEYWORDS
                                    ),
                                )
                            ]
                        )
                    ],
                    tool_config=types.ToolConfig(
                        function_calling_config=types.FunctionCallingConfig(
                            mode=types.FunctionCallingConfigMode.ANY,
                            allowed_function_names=[tool.name],
                        )
                    ),
                ),
            )
        except Exception as exc:  # SDK raises its own hierarchy; classify by status code
            raise _classify_gemini(exc) from None
        calls = [c for c in (response.function_calls or []) if c.name == tool.name]
        if len(calls) != 1:
            raise LLMError(
                "invalid_output", f"Expected one '{tool.name}' call, got {len(calls)}.", "gemini"
            )
        usage = response.usage_metadata
        return ToolCallResult(
            arguments=_parse_arguments(tool, dict(calls[0].args or {}), "gemini"),
            provider="gemini",
            model=self.model,
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


def _classify_gemini(exc: Exception) -> LLMError:
    code = getattr(exc, "code", None)
    name = type(exc).__name__
    if code == 429:
        return LLMError("quota", "Gemini quota exhausted or rate limited.", "gemini")
    if code in (401, 403):
        return LLMError("configuration", "Gemini rejected the API key.", "gemini")
    if code == 404:
        return LLMError("configuration", "Gemini model not available for this key.", "gemini")
    if code is None or (isinstance(code, int) and code >= 500) or "Timeout" in name:
        return LLMError("transient", f"Gemini unavailable ({name}).", "gemini")
    return LLMError("provider", f"Gemini request failed ({code}).", "gemini")


@dataclass
class _Spend:
    at: float
    tokens: int


class TokenRateLimiter:
    """Stays under a tokens-per-minute budget by waiting *before* a call instead of after a 429.

    Tokens spent in the last minute are tracked; a call that would exceed the budget waits until
    enough older spend has aged out. An idle limiter never delays anything. One instance is shared
    per process (`_shared_limiter`) because a client is built for every request.
    """

    WINDOW_SECONDS = 60.0

    def __init__(
        self,
        tokens_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._budget = tokens_per_minute
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._spend: list[_Spend] = []

    def acquire(self, estimate: int) -> _Spend:
        """Blocks until `estimate` tokens fit in the window, then reserves them."""
        while True:
            with self._lock:
                now = self._clock()
                self._spend = [s for s in self._spend if now - s.at < self.WINDOW_SECONDS]
                excess = sum(s.tokens for s in self._spend) + estimate - self._budget
                # A single call larger than the whole budget must still be allowed through.
                if excess <= 0 or not self._spend:
                    reservation = _Spend(now, estimate)
                    self._spend.append(reservation)
                    return reservation
                freed = 0
                for s in self._spend:
                    freed += s.tokens
                    if freed >= excess:
                        wait = s.at + self.WINDOW_SECONDS - now
                        break
            log.info("Groq token budget reached; waiting %.0f s before the next call", wait)
            self._sleep(wait)

    def settle(self, reservation: _Spend, actual_tokens: int) -> None:
        """Replaces the estimate with what the provider reported."""
        with self._lock:
            reservation.tokens = actual_tokens


_limiters: dict[int, TokenRateLimiter] = {}
_limiters_lock = threading.Lock()


def _shared_limiter(tokens_per_minute: int) -> TokenRateLimiter:
    with _limiters_lock:
        return _limiters.setdefault(tokens_per_minute, TokenRateLimiter(tokens_per_minute))


# Proposals so far produced 600-2,000 output tokens; used only until the real count is known.
EXPECTED_OUTPUT_TOKENS = 1500


class GroqClient:
    """Groq's OpenAI-compatible chat completions API over httpx (no extra SDK)."""

    provider = "groq"
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        limiter: TokenRateLimiter | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._transport = transport
        self._sleep = sleep
        self._limiter = limiter

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                return client.post(
                    self.URL, json=payload, headers={"Authorization": f"Bearer {self._api_key}"}
                )
        except httpx.HTTPError as exc:
            raise LLMError(
                "transient", f"Groq unreachable ({type(exc).__name__}).", "groq"
            ) from None

    def call_tool(self, system: str, user: str, tool: ToolSpec) -> ToolCallResult:
        started = time.perf_counter()
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": inline_json_schema(tool.args_model),
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": tool.name}},
        }
        reservation = None
        if self._limiter is not None:
            estimate = len(json.dumps(payload)) // 3 + EXPECTED_OUTPUT_TOKENS
            reservation = self._limiter.acquire(estimate)
        response = self._post(payload)
        if response.status_code == 429:
            # Per-minute rate limits clear quickly: wait once if the server says it is short.
            wait = _retry_after_seconds(response)
            if wait is not None and wait <= MAX_RATE_LIMIT_WAIT_SECONDS:
                self._sleep(wait)
                response = self._post(payload)
        if response.status_code == 429:
            raise LLMError("quota", "Groq rate limited.", "groq")
        if response.status_code in (401, 403):
            raise LLMError("configuration", "Groq rejected the API key.", "groq")
        if response.status_code >= 500:
            raise LLMError("transient", f"Groq unavailable ({response.status_code}).", "groq")
        if response.status_code >= 400:
            raise LLMError("provider", f"Groq request failed ({response.status_code}).", "groq")
        body = response.json()
        try:
            calls = body["choices"][0]["message"].get("tool_calls") or []
            matching = [c for c in calls if c["function"]["name"] == tool.name]
        except (KeyError, IndexError, TypeError):
            raise LLMError("invalid_output", "Unexpected Groq response shape.", "groq") from None
        if len(matching) != 1:
            raise LLMError(
                "invalid_output", f"Expected one '{tool.name}' call, got {len(matching)}.", "groq"
            )
        usage = body.get("usage") or {}
        if self._limiter is not None and reservation is not None and usage.get("total_tokens"):
            self._limiter.settle(reservation, int(usage["total_tokens"]))
        return ToolCallResult(
            arguments=_parse_arguments(tool, matching[0]["function"]["arguments"], "groq"),
            provider="groq",
            model=self.model,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


MAX_RATE_LIMIT_WAIT_SECONDS = 20.0


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None


class FallbackLLMClient:
    """Tries providers in order. Every failure is logged by category; the last error is raised."""

    def __init__(self, clients: Sequence[LLMClient]) -> None:
        if not clients:
            raise LLMError("configuration", "No LLM provider is configured.", "none")
        self._clients = clients
        self.provider = "+".join(c.provider for c in clients)
        self.model = clients[0].model

    def call_tool(self, system: str, user: str, tool: ToolSpec) -> ToolCallResult:
        failures: list[tuple[str, str]] = []
        last: LLMError | None = None
        for client in self._clients:
            try:
                result = client.call_tool(system, user, tool)
            except LLMError as exc:
                log.warning("LLM provider %s failed: %s", exc.provider, exc.category)
                failures.append((exc.provider, exc.category))
                last = exc
                continue
            return replace(result, fallbacks=tuple(failures))
        assert last is not None  # noqa: S101 - loop ran at least once
        raise last


def build_llm_client() -> LLMClient:
    """Provider order from LLM_PROVIDERS (default "groq,gemini"); providers without keys skipped.

    Defaults chosen from the 2026-10-13 live comparison (docs/evidence/): Groq gpt-oss-120b was
    the most accurate and fast; Gemini 3.5 Flash Lite is the backup because its free-tier quota
    (500/day) and ~3 s latency make it available when Groq is rate limited.
    """
    get_settings()  # loads the git-ignored .env (keys) for local runs, whoever calls first
    timeout = float(os.environ.get("LLM_TIMEOUT_SECONDS", "90"))
    clients: list[LLMClient] = []
    for name in os.environ.get("LLM_PROVIDERS", "groq,gemini").split(","):
        name = name.strip().lower()
        if name == "gemini" and os.environ.get("GEMINI_API_KEY"):
            clients.append(
                GeminiClient(
                    os.environ["GEMINI_API_KEY"],
                    os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite"),
                    timeout,
                )
            )
        elif name == "groq" and os.environ.get("GROQ_API_KEY"):
            # Free tier is 8,000 tokens/min; stay a little under it. 0 disables pacing.
            budget = int(os.environ.get("GROQ_TOKENS_PER_MINUTE", "7000"))
            clients.append(
                GroqClient(
                    os.environ["GROQ_API_KEY"],
                    os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
                    timeout,
                    limiter=_shared_limiter(budget) if budget > 0 else None,
                )
            )
    return FallbackLLMClient(clients)
