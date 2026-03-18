# Plan: Move prompts to Langfuse Prompt Management

## Goal
Move the two main prompts from hardcoded constants in `embedding.py` to Langfuse Prompt Management, so you can edit/version/A/B test them from the Langfuse dashboard — no redeploy needed.

## Prompts to move

| # | Current constant | Langfuse prompt name | Used by |
|---|-----------------|---------------------|---------|
| 1 | `CLASSIFY_AND_DESCRIBE_PROMPT` | `classify-and-describe` | `classify_and_describe()` → main pipeline |
| 2 | `DESCRIBE_PROMPT` | `describe-package` | `describe_package()` → skip-classification flow |

> `CLASSIFY_PROMPT` is left as-is (simple, rarely changes, only used for ref image upload).

## Changes

### 1. Add helper to fetch prompts from Langfuse (with fallback)

**File:** `backend/app/services/langfuse_client.py`

Add a `get_prompt(name, fallback)` function that:
- Calls `get_langfuse().get_prompt(name)` to fetch the latest prompt version
- If Langfuse is not configured or fetch fails → returns the hardcoded `fallback` string
- This means the app **never breaks** if Langfuse is down or keys are missing

```python
def get_prompt(name: str, *, fallback: str) -> str:
    """Fetch a managed prompt from Langfuse, falling back to hardcoded default."""
    client = get_langfuse()
    if client is None:
        return fallback
    try:
        prompt = client.get_prompt(name)
        return prompt.compile()
    except Exception:
        logger.warning("Failed to fetch Langfuse prompt '%s', using fallback", name)
        return fallback
```

### 2. Update `embedding.py` to use managed prompts

**File:** `backend/app/services/embedding.py`

- Keep `CLASSIFY_AND_DESCRIBE_PROMPT` and `DESCRIBE_PROMPT` as **fallback** constants (renamed with `_DEFAULT` suffix for clarity)
- In `classify_and_describe()` and `describe_package()`, call `get_prompt(...)` instead of using the constant directly

Changes in `classify_and_describe()`:
```python
from app.services.langfuse_client import get_prompt

prompt = get_prompt("classify-and-describe", fallback=CLASSIFY_AND_DESCRIBE_DEFAULT)
raw_text = _call_vision(image, prompt)
```

Same pattern in `describe_package()`:
```python
prompt = get_prompt("describe-package", fallback=DESCRIBE_DEFAULT)
raw_text = _call_vision(image, prompt)
```

### 3. Seed initial prompt versions in Langfuse (one-time)

After deploying, you manually create the two prompts in the Langfuse dashboard:
1. Go to **cloud.langfuse.com → Prompts → New Prompt**
2. Create `classify-and-describe` with the current prompt text as v1
3. Create `describe-package` with the current prompt text as v1

(Or we can add a one-time seed script — your choice.)

### 4. Update tests

**File:** `backend/tests/test_langfuse.py`

Add a test that verifies `get_prompt()` returns the fallback when Langfuse is disabled.

## How you'll use this

1. **Edit a prompt**: Go to Langfuse → Prompts → edit `classify-and-describe` → save as v2
2. **Compare versions**: In Traces, each trace is tagged with the prompt version — filter and compare output quality
3. **Roll back**: Set the active version back to v1 if v2 performs worse
4. **No redeploy needed** — the backend fetches the latest prompt on each call

## Files changed
- `backend/app/services/langfuse_client.py` — add `get_prompt()`
- `backend/app/services/embedding.py` — use `get_prompt()` with fallbacks
- `backend/tests/test_langfuse.py` — add fallback test
