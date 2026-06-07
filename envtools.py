"""Tiny .env reader/writer with no external dependencies."""
import os
from pathlib import Path

ENV_PATH = Path(__file__).with_name(".env")


def load_env() -> dict:
    """Load .env into os.environ and return it as a dict."""
    data = {}
    if not ENV_PATH.exists():
        return data
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        data[key] = value
        os.environ[key] = value
    return data


def set_env_value(key: str, value: str) -> None:
    """Update or append a single KEY=value line in .env, preserving the rest."""
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    out, found = [], False
    for line in lines:
        if line.strip().startswith(f"{key}=") or line.strip().startswith(f"{key} ="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(out) + "\n")
    os.environ[key] = value
