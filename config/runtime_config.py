from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CFG_PATH = _REPO_ROOT / "config" / "analysis_defaults.yaml"
_CFG_CACHE: dict[str, Any] | None = None


def _resolve_cfg_path() -> Path:
    override = os.getenv("STOCK_ANALYSIS_CRYPTO_CONFIG", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_CFG_PATH


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except Exception:
        return {}
    if not path.is_file():
        return {}
    try:
        obj = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def get_analysis_config(*, force_reload: bool = False) -> dict[str, Any]:
    global _CFG_CACHE
    if force_reload or _CFG_CACHE is None:
        _CFG_CACHE = _load_yaml(_resolve_cfg_path())
    return _CFG_CACHE


def get_ma_system() -> dict[str, Any]:
    cfg = get_analysis_config()
    node = cfg.get("ma_system")
    return node if isinstance(node, dict) else {}


def get_min_journal_rr(default: float = 1.2) -> float:
    cfg = get_analysis_config()
    v = cfg.get("min_journal_rr", default)
    try:
        x = float(v)
        return x if x > 0 else float(default)
    except (TypeError, ValueError):
        return float(default)


def get_journal_quality() -> dict[str, Any]:
    cfg = get_analysis_config()
    node = cfg.get("journal_quality")
    return node if isinstance(node, dict) else {}


def get_journal_action_thresholds() -> tuple[float, float]:
    cfg = get_analysis_config()
    node = cfg.get("journal_action_thresholds")
    if not isinstance(node, dict):
        return 1.45, 1.2
    worth = node.get("worth_doing_rr")
    observe = node.get("observe_rr")
    worth_v = float(worth) if isinstance(worth, (int, float)) else 1.45
    observe_v = float(observe) if isinstance(observe, (int, float)) else 1.2
    if worth_v < observe_v:
        worth_v = observe_v
    return worth_v, observe_v

