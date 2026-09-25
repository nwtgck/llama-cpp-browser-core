"""Shared browser compiler pins; runtime upstream pins are deliberately separate."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load_toolchain(repo: Path = ROOT) -> dict:
    return json.loads((repo / 'toolchain/config.json').read_text())

def runtime_toolchain(runtime: Path) -> dict:
    """Keep the existing llama provenance schema without duplicating shared pins."""
    shared = load_toolchain(runtime.parent)
    local = json.loads((runtime / 'config/toolchain.json').read_text())
    if shared.keys() & local.keys():
        raise ValueError('Runtime configuration must not override shared toolchain pins')
    return {**shared, **local}
