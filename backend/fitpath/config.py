"""Paths and settings shared by every FitPath module."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
RAW = DATA / "raw"
PARQUET = DATA / "parquet"
OUT = DATA / "out"

for _d in (PARQUET, OUT):
    _d.mkdir(parents=True, exist_ok=True)

# sales_channel_id is undocumented in the H&M dataset; we treat 1 as the physical store
STORE_CHANNEL = int(os.getenv("FITPATH_STORE_CHANNEL", "1"))

# LLM provider:
#   "amd"    AMD Token Factory (OpenAI-compatible API on AMD GPUs): AMD_API_KEY, AMD_BASE_URL, AMD_MODEL
#   "openai" any other OpenAI-compatible endpoint (e.g. vLLM on ROCm): OPENAI_BASE_URL, OPENAI_API_KEY, LLM_MODEL
#   "gemini" Google AI Studio: GEMINI_API_KEY, LLM_MODEL
AMD_API_KEY = os.getenv("AMD_API_KEY", "").strip()
AMD_BASE_URL = os.getenv("AMD_BASE_URL", "https://developer.amd.com.cn/radeon/api/v1").rstrip("/")
AMD_MODEL = os.getenv("AMD_MODEL", "DeepSeek-V4-Flash")
AMD_FALLBACK_MODELS = os.getenv("AMD_FALLBACK_MODELS", "Qwen3.8-Flash-Next")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "amd" if AMD_API_KEY else "gemini")
# Gemma 4 has a high free daily quota and is open-weight, so it can later run on AMD GPUs
LLM_MODEL = os.getenv("LLM_MODEL", "gemma-4-26b-a4b-it")
LLM_FALLBACK_MODELS = os.getenv("LLM_FALLBACK_MODELS", "gemini-3.1-flash-lite")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


def pq(name: str) -> str:
    """Parquet path as a forward-slash string, ready for DuckDB SQL."""
    return (PARQUET / f"{name}.parquet").as_posix()
