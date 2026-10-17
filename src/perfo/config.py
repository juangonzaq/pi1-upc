"""Configuration loading: default.yaml + thresholds.yaml + profile overrides."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml


def find_project_root(start: Path | None = None) -> Path:
    here = (start or Path.cwd()).resolve()
    for p in [here, *here.parents]:
        if (p / "configs" / "default.yaml").exists():
            return p
    pkg_root = Path(__file__).resolve().parents[2]
    if (pkg_root / "configs" / "default.yaml").exists():
        return pkg_root
    raise FileNotFoundError("No se encontró configs/default.yaml; ejecute perfo desde la raíz del proyecto")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config(dict):
    """Nested dict with dotted access: cfg.get_path("training.max_steps")."""

    root: Path

    def get_path(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def resolve(self, rel: str | Path) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.root / p


def load_config(profile: str | None = None, overrides: dict[str, Any] | None = None, root: Path | None = None) -> Config:
    root = root or find_project_root()
    data = yaml.safe_load((root / "configs" / "default.yaml").read_text()) or {}
    data["thresholds"] = yaml.safe_load((root / "configs" / "thresholds.yaml").read_text()) or {}
    if profile:
        pfile = root / "configs" / "profiles" / f"{profile}.yaml"
        if not pfile.exists():
            raise FileNotFoundError(f"Perfil no encontrado: {pfile}")
        data = deep_merge(data, yaml.safe_load(pfile.read_text()) or {})
        data["profile"] = profile
    data = deep_merge(data, overrides or {})
    cfg = Config(data)
    cfg.root = root
    return cfg


def set_path(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = data
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value
