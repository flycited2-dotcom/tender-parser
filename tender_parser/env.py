from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path) -> list[str]:
    if not path.exists():
        return []

    loaded: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _unquote(value.strip())
        loaded.append(key)
    return loaded


def get_env_status(keys: list[str]) -> dict[str, bool]:
    return {key: bool(os.getenv(key, "")) for key in keys}


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
