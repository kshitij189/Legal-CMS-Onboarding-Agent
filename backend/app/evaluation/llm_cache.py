"""Record/replay for live LLM calls in the evaluation suite.

`live` calls the configured provider chain and records each response; `replay` answers only from
the recording (deterministic, free, CI-friendly). Entries are keyed by a hash of the exact prompt
and tool, so a prompt change (and a PROMPT_VERSION bump) invalidates old recordings. Recorded
content is synthetic schema metadata and model output only — never secrets.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agent.llm_client import LLMClient, LLMError, ToolCallResult
from app.agent.tool_registry import ToolSpec


def prompt_key(system: str, user: str, tool: ToolSpec) -> str:
    return hashlib.sha256(
        json.dumps([system, user, tool.name], ensure_ascii=False).encode()
    ).hexdigest()


class CachedLLM:
    def __init__(self, path: Path, mode: str, inner: LLMClient | None = None) -> None:
        if mode not in ("live", "replay"):
            raise ValueError("mode must be 'live' or 'replay'")
        if mode == "live" and inner is None:
            raise ValueError("live mode needs a real LLM client")
        self._path = path
        self._mode = mode
        self._inner = inner
        self._entries: dict[str, dict[str, Any]] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )
        self.provider = inner.provider if inner else "replay"
        self.model = inner.model if inner else "replay"

    def call_tool(self, system: str, user: str, tool: ToolSpec) -> ToolCallResult:
        key = prompt_key(system, user, tool)
        if self._mode == "replay":
            entry = self._entries.get(key)
            if entry is None:
                raise LLMError(
                    "configuration",
                    "No recorded response for this prompt; run with --llm live.",
                    "replay",
                )
            return ToolCallResult(
                arguments=tool.args_model.model_validate(entry["arguments"]),
                provider=entry["provider"],
                model=entry["model"],
                input_tokens=entry.get("input_tokens"),
                output_tokens=entry.get("output_tokens"),
                latency_ms=entry.get("latency_ms", 0),
                fallbacks=tuple((f[0], f[1]) for f in entry.get("fallbacks", [])),
            )
        assert self._inner is not None  # noqa: S101
        result = self._inner.call_tool(system, user, tool)
        self._entries[key] = {
            "provider": result.provider,
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "latency_ms": result.latency_ms,
            "fallbacks": [list(f) for f in result.fallbacks],
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "arguments": result.arguments.model_dump(mode="json"),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._entries, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        return result
