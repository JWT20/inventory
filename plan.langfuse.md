# Langfuse Integration Plan for WijnPick

## Why Langfuse?

WijnPick uses Google Gemini for two critical AI operations:
1. **Vision classification/description** — identifying wine boxes from photos
2. **Embedding generation** — converting descriptions to vectors for similarity search

Currently, these calls are only tracked via basic `[TIMING]` log messages. Langfuse will provide:
- Full trace visibility into every AI call (latency, tokens, cost)
- Quality monitoring of vision descriptions and match accuracy
- Prompt version management for the Gemini prompts
- User-level analytics (which users trigger most AI calls, error rates)
- Score tracking for match confidence and description quality

---

## Implementation Steps

### Step 1: Add Langfuse dependency

**File:** `backend/requirements.txt`

Add `langfuse` to the Python dependencies.

### Step 2: Add Langfuse configuration

**File:** `backend/app/config.py`

Add three new environment variables to `Settings`:
- `LANGFUSE_PUBLIC_KEY` (default: empty string — disabled when empty)
- `LANGFUSE_SECRET_KEY` (default: empty string)
- `LANGFUSE_HOST` (default: `https://cloud.langfuse.com`)

### Step 3: Create Langfuse client module

**New file:** `backend/app/services/langfuse_client.py`

- Singleton Langfuse client, initialized lazily (like the Gemini client pattern)
- Graceful degradation: if keys are not set, return a no-op stub so all tracing calls are silently skipped (same pattern as Kafka in `events.py`)
- Export helper: `get_langfuse()` → returns `Langfuse` instance or `None`

### Step 4: Instrument the embedding service (core integration)

**File:** `backend/app/services/embedding.py`

This is where all Gemini API calls happen. Wrap each function with Langfuse tracing:

| Function | Langfuse concept | What to capture |
|----------|-----------------|-----------------|
| `process_image()` | **Trace** (top-level) | Full pipeline duration, final result (is_package, description quality) |
| `classify_and_describe()` | **Generation** (child span) | Prompt text, Gemini response, model name, token usage |
| `describe_package()` | **Generation** (child span) | Prompt text, Gemini response, model name |
| `generate_embedding()` | **Generation** (child span) | Input text, model name, embedding dimensions |
| `assess_description_quality()` | **Span** (child) | Quality score output |

Implementation approach — use the **Langfuse `@observe()` decorator** on each function:
```python
from langfuse.decorators import observe, langfuse_context

@observe()
def process_image(image_bytes):
    # existing code — Langfuse auto-captures duration
    langfuse_context.update_current_observation(
        metadata={"image_size": len(image_bytes)}
    )
```

For Gemini API calls specifically, manually log generations:
```python
langfuse_context.update_current_observation(
    model=settings.GEMINI_VISION_MODEL,
    input=prompt_text,
    output=response_text,
    usage={"input": input_tokens, "output": output_tokens}  # if available from Gemini response
)
```

### Step 5: Instrument the matching service

**File:** `backend/app/services/matching.py`

Add an `@observe()` span around the vector search to capture:
- Number of candidates returned
- Top match score and SKU
- Search duration
- Threshold used

### Step 6: Instrument API endpoints (trace context)

**Files:** `backend/app/routers/receiving.py`, `backend/app/routers/vision.py`

Create traces at the router level so each user request gets a full trace:

```python
@observe()
async def identify_box(request):
    langfuse_context.update_current_trace(
        user_id=str(current_user.id),
        session_id=str(order_id),  # group traces by order
        metadata={"endpoint": "/api/receiving/identify"}
    )
```

Endpoints to instrument:
- `POST /api/receiving/identify` — box identification
- `POST /api/receiving/book` — booking (includes re-identification)
- `POST /api/vision/identify` — ad-hoc vision identification
- `POST /api/skus/{id}/reference-images` — reference image upload (embedding generation)

### Step 7: Add quality scores

After a booking is confirmed (user accepts a match), push a score back to Langfuse:

```python
langfuse.score(
    trace_id=trace_id,
    name="match_accepted",
    value=1,  # or 0 if user rejected
)
langfuse.score(
    trace_id=trace_id,
    name="match_confidence",
    value=similarity_score,  # 0.0 - 1.0
)
```

This enables tracking match accuracy over time.

### Step 8: Update environment configuration

**Files:** `.env.example`, `docker-compose.yml`

Add the three Langfuse env vars to:
- `.env.example` with documentation comments
- `docker-compose.yml` backend service environment section

### Step 9: Add tests

**File:** `backend/tests/test_langfuse.py`

- Test that Langfuse client gracefully returns `None` when keys are not configured
- Test that `@observe()` decorated functions still work when Langfuse is disabled
- Mock Langfuse client in existing embedding/matching tests

---

## Architecture Diagram

```
User Request
    │
    ▼
┌──────────────────┐
│  Router endpoint  │ ◄── Langfuse Trace (user_id, session_id)
│  (receiving.py)   │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  process_image()  │ ◄── Langfuse Span
│  (embedding.py)   │
├──────────────────┤
│ classify_and_     │ ◄── Langfuse Generation (prompt, response, model, tokens)
│ describe()        │
├──────────────────┤
│ generate_         │ ◄── Langfuse Generation (input text, model)
│ embedding()       │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  find_matches()   │ ◄── Langfuse Span (candidates, top score)
│  (matching.py)    │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Response + Score │ ◄── Langfuse Score (match_accepted, confidence)
└──────────────────┘
```

---

## Files Changed Summary

| File | Change |
|------|--------|
| `backend/requirements.txt` | Add `langfuse` |
| `backend/app/config.py` | Add 3 env vars |
| `backend/app/services/langfuse_client.py` | **New** — client singleton + graceful degradation |
| `backend/app/services/embedding.py` | Add `@observe()` decorators + generation logging |
| `backend/app/services/matching.py` | Add `@observe()` span |
| `backend/app/routers/receiving.py` | Add trace context (user, session) |
| `backend/app/routers/vision.py` | Add trace context |
| `backend/app/routers/skus.py` | Add trace context for reference image upload |
| `.env.example` | Add Langfuse env vars |
| `docker-compose.yml` | Add Langfuse env vars to backend service |
| `backend/tests/test_langfuse.py` | **New** — graceful degradation tests |

---

## Key Design Decisions

1. **Decorator-based (`@observe()`)** — minimal code changes, automatic nesting of spans
2. **Graceful degradation** — mirrors the existing Kafka pattern; no Langfuse keys = no tracing, no errors
3. **No OpenTelemetry** — Langfuse's native Python SDK is simpler and purpose-built for LLM tracing; OTEL would be overkill here
4. **Trace per request** — each API call creates one trace with nested spans, giving end-to-end visibility
5. **Scores on user feedback** — when a user accepts/rejects a match, we record it as a Langfuse score for quality monitoring
