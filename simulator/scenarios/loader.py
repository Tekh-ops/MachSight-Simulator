"""
simulator/scenarios/loader.py
==============================
Scenario loader: discovers YAML files and returns scenario dicts.

Scenario search order:
    1. ./scenarios/*.yaml          (user-provided, project-root)
    2. simulator/scenarios/definitions/*.yaml  (built-in)

Later files with the same name override earlier ones (user overrides builtins).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# Built-in scenario definitions are co-located with this module
_BUILTIN_DIR = Path(__file__).parent / "definitions"

# User-supplied scenarios live at the project root
_USER_DIR = Path("scenarios")


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "duration_seconds" in data and "duration_s" not in data:
        data["duration_s"] = data["duration_seconds"]
    elif "duration_s" in data and "duration_seconds" not in data:
        data["duration_seconds"] = data["duration_s"]
    return data


def discover_scenarios() -> Dict[str, Path]:
    """Return mapping of scenario_name → file path for all known scenarios."""
    found: Dict[str, Path] = {}

    # Builtins first
    for p in sorted(_BUILTIN_DIR.glob("*.yaml")):
        name = p.stem
        found[name] = p

    # User overrides
    if _USER_DIR.exists():
        for p in sorted(_USER_DIR.glob("*.yaml")):
            name = p.stem
            found[name] = p
            if name in found:
                logger.info("User scenario '%s' overrides builtin", name)

    return found


def list_scenarios() -> List[str]:
    """Return sorted list of available scenario names."""
    return sorted(discover_scenarios().keys())


def load_scenario(name: str) -> dict:
    """Load and return a scenario dict by name.

    Raises KeyError if the scenario is not found.
    """
    scenarios = discover_scenarios()
    if name not in scenarios:
        raise KeyError(f"Scenario not found: {name!r}. Available: {list_scenarios()}")
    path = scenarios[name]
    data = _load_yaml(path)
    logger.info("Loaded scenario '%s' from %s", name, path)
    return data


def load_scenario_from_file(path: str) -> dict:
    """Load a scenario directly from a file path."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Scenario file not found: {path}")
    return _load_yaml(p)
