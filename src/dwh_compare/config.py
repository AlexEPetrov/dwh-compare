"""Load YAML config and registry."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from dwh_compare.models import AppConfig, Registry

_ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")


def _expand_env(value: str) -> str:
    """Expand ${VAR} and ${VAR:-default} placeholders."""

    def repl(m: re.Match[str]) -> str:
        name, default = m.group(1), m.group(2)
        env = os.environ.get(name)
        if env is not None:
            return env
        if default is not None:
            return default
        return m.group(0)

    return _ENV_PATTERN.sub(repl, value)


def _expand_tree(obj: Any) -> Any:
    if isinstance(obj, str):
        return _expand_env(obj)
    if isinstance(obj, list):
        return [_expand_tree(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _expand_tree(v) for k, v in obj.items()}
    return obj


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return _expand_tree(data)


def load_config(path: str | Path) -> AppConfig:
    data = _load_yaml(Path(path))
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return AppConfig.model_validate(data)


def load_registry(path: str | Path) -> Registry:
    data = _load_yaml(Path(path))
    if isinstance(data, list):
        data = {"tables": data}
    elif not isinstance(data, dict):
        raise ValueError(f"Expected mapping or list in {path}")
    return Registry.model_validate(data)
