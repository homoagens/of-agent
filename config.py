# config.py — global parameters for the OF-Agent framework.
#
# All sensitive values are read from environment variables.
# Copy .env.example to .env and fill in your values, or export them
# in your shell before running.

import os
from pathlib import Path

# Load a .env file sitting next to this module, if present, so the values
# written by configure.sh / configure.bat are actually picked up.
# Existing environment variables always take precedence over the file.
def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()

DEBUG = False

# ─────────────────────────────────────────────
# LLM BACKEND
# ─────────────────────────────────────────────
# BACKEND_URL  : OpenAI-compatible API endpoint (local llama.cpp, LM Studio,
#                Ollama, OpenRouter, or any proxy).
# BACKEND_KEY  : API key (required by some backends; use any string for local).

BACKEND_URL = os.environ.get("OF_AGENT_BACKEND_URL", "http://localhost:11434")
BACKEND_KEY = os.environ.get("OF_AGENT_BACKEND_KEY", "local")

# ─────────────────────────────────────────────
# DEFAULT MODEL
# ─────────────────────────────────────────────
# Model identifier passed in the "model" field of each LLM request.
# Must match a model available on your backend.
# Examples: "llama3.1:8b", "qwen2.5:14b", "gemma3:12b"

DEFAULT_MODEL       = os.environ.get("OF_AGENT_MODEL", "llama3.1:8b")
DEFAULT_TEMPERATURE = float(os.environ.get("OF_AGENT_TEMPERATURE", "0.2"))

# Anti-repetition sampling (standard OpenAI-compatible parameters, supported by
# llama.cpp, vLLM, Ollama, etc.). Small thinking models can degenerate into an
# endless repetition loop inside their reasoning, never emitting an answer and
# burning the whole token budget. A modest frequency penalty breaks that loop.
# Set to 0 to disable (the parameter is then omitted from the request).
FREQUENCY_PENALTY = float(os.environ.get("OF_AGENT_FREQUENCY_PENALTY", "0.4"))
PRESENCE_PENALTY  = float(os.environ.get("OF_AGENT_PRESENCE_PENALTY", "0.0"))

# ─────────────────────────────────────────────
# GENERAL PARAMETERS
# ─────────────────────────────────────────────

MAX_TOKENS = 4096
TIMEOUT    = 300   # seconds — increase for models with large context windows

# Maximum steps in the ReAct loop before a forced verdict is requested.
MAX_STEPS = 15

# Memory compression thresholds (see memory.py).
MAX_MESSAGES    = 30
MAX_CHARS       = 150000  # ~38k tokens
MESSAGES_RECENT = 6       # messages always kept verbatim (never compressed)
