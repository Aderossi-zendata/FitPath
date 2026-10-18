"""One small interface over the LLM backends FitPath can use.

LLM_PROVIDER=amd     -> AMD Token Factory, an OpenAI-compatible API served on AMD GPUs (AMD_API_KEY, AMD_BASE_URL)
LLM_PROVIDER=openai  -> any other OpenAI-compatible endpoint (OPENAI_BASE_URL, OPENAI_API_KEY), e.g. vLLM on ROCm
LLM_PROVIDER=gemini  -> Google AI Studio (GEMINI_API_KEY), through its OpenAI-compatible endpoint

The main model is tried first, then each fallback (comma separated). A model is retried once on server
errors (5xx), and skipped on quota or not-found errors.
"""
import json
import time

from fitpath import config

GEMINI_OPENAI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class LLMUnavailable(RuntimeError):
    pass


def models() -> list[str]:
    """Models to try, in order, for the configured provider."""
    if config.LLM_PROVIDER == "amd":
        main, extra = config.AMD_MODEL, config.AMD_FALLBACK_MODELS
    else:
        main, extra = config.LLM_MODEL, config.LLM_FALLBACK_MODELS
    return list(dict.fromkeys([main, *[m.strip() for m in extra.split(",") if m.strip()]]))


def endpoint() -> tuple[str, str]:
    """(base_url, api_key) of the configured provider."""
    if config.LLM_PROVIDER == "amd":
        if not config.AMD_API_KEY:
            raise LLMUnavailable("AMD_API_KEY is not set")
        return config.AMD_BASE_URL, config.AMD_API_KEY
    if config.LLM_PROVIDER == "openai":
        if not config.OPENAI_BASE_URL:
            raise LLMUnavailable("OPENAI_BASE_URL is not set")
        return config.OPENAI_BASE_URL, config.OPENAI_API_KEY or "none"
    if config.LLM_PROVIDER == "gemini":
        if not config.GEMINI_API_KEY:
            raise LLMUnavailable("GEMINI_API_KEY is not set")
        return GEMINI_OPENAI_URL, config.GEMINI_API_KEY
    raise LLMUnavailable(f"unknown provider: {config.LLM_PROVIDER}")


def client(timeout: float = 120):
    from openai import OpenAI
    base, key = endpoint()
    return OpenAI(base_url=base, api_key=key, timeout=timeout)


def status_code(e: Exception):
    return getattr(e, "status_code", None) or getattr(e, "code", None)


def _retryable(e: Exception) -> bool:
    code = status_code(e)
    return isinstance(code, int) and code >= 500 or type(e).__name__ in ("APITimeoutError", "APIConnectionError", "ReadTimeout")


def _json_call(cl, model: str, system: str, prompt: str, temperature: float) -> str:
    msgs = [{"role": "system", "content": system + "\nReply with one JSON object only."}, {"role": "user", "content": prompt}]
    try:
        resp = cl.chat.completions.create(model=model, temperature=temperature, messages=msgs, response_format={"type": "json_object"})
    except Exception as e:      # some servers do not support response_format: ask in the prompt only
        if status_code(e) != 400:
            raise
        resp = cl.chat.completions.create(model=model, temperature=temperature, messages=msgs)
    return resp.choices[0].message.content or ""


def _parse(text: str) -> dict:
    text = text.strip()
    if "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    if not text.startswith("{"):
        i, j = text.find("{"), text.rfind("}")
        if i >= 0 and j > i:
            text = text[i:j + 1]
    return json.loads(text)


def complete_json(system: str, prompt: str, temperature: float = .4) -> tuple[dict, str]:
    """Ask the configured models in order for a JSON object; returns (parsed JSON, model that answered)."""
    cl = client()
    errors = []
    for model in models():
        for attempt in range(2):
            try:
                return _parse(_json_call(cl, model, system, prompt, temperature)), model
            except json.JSONDecodeError:
                errors.append(f"{model}: invalid JSON")
            except Exception as e:  # quota, not found, network, server errors
                errors.append(f"{model}: {status_code(e) or type(e).__name__}")
                if attempt == 0 and _retryable(e):
                    time.sleep(2)
                    continue
            break
    raise LLMUnavailable("no model answered (" + "; ".join(errors) + ")")


def describe() -> dict:
    try:
        endpoint()
        ok = True
    except LLMUnavailable:
        ok = False
    return {"provider": config.LLM_PROVIDER, "model": models()[0], "fallbacks": models()[1:], "configured": ok}
