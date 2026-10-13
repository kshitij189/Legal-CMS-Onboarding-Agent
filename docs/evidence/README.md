# Evidence

Recorded outputs of live checks, kept so results can be inspected and compared. Synthetic data only;
agreement with the labelled answers is not proof of real-world accuracy.

| File | What it is |
|---|---|
| `phase3-live-llm-run.json` | Latest live mapping-proposal run with the default provider chain (Groq `openai/gpt-oss-120b` → Gemini `gemini-3.5-flash-lite`). Regenerate with `python -m pytest -m live_llm -s` from `backend/`. |
| `phase3-live-llm-run-gemini-3.8-flash.json` | 2026-10-13 comparison run with `GEMINI_MODEL=gemini-3.8-flash` as primary (firm B fell back to Groq after a Gemini timeout). |
| `phase3-live-llm-run-gemini-3.5-flash-lite.json` | 2026-10-13 comparison run with `GEMINI_MODEL=gemini-3.5-flash-lite` as primary. |

Model choice (decision D1, revised 2026-10-13), same prompt `mapping-v1`, firms A–C:

| Model | Free-tier limits (observed) | Latency per firm | Clearly mappable correct | Invalid proposals | Agreement |
|---|---|---|---|---|---|
| Groq `openai/gpt-oss-120b` | 1,000 req/day, 8K tokens/min | ~4–20 s | 23/23 | 0 | 26/30 |
| Gemini `gemini-3.8-flash` | 5 req/min, 20 req/day | 171–212 s | 15/15 (A, C) | 0 | 16/20 (A, C) |
| Gemini `gemini-3.5-flash-lite` | 15 req/min, 500 req/day | ~2.5 s | 22/23 | 5 | 23/30 |

One proposal uses ~3.5–4.3K tokens, so Groq's token-per-minute limit allows about two proposals per
minute; short rate limits are waited out once, longer ones fall back to Gemini.
