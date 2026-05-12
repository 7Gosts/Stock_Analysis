from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CFG_PATH = _REPO_ROOT / "config" / "analysis_defaults.yaml"
_DEFAULT_EXAMPLE_CFG_PATH = _REPO_ROOT / "config" / "analysis_defaults.example.yaml"
_CFG_CACHE: dict[str, Any] | None = None


def _resolve_cfg_path() -> Path:
    override = os.getenv("STOCK_ANALYSIS_CRYPTO_CONFIG", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if _DEFAULT_CFG_PATH.is_file():
        return _DEFAULT_CFG_PATH
    if _DEFAULT_EXAMPLE_CFG_PATH.is_file():
        return _DEFAULT_EXAMPLE_CFG_PATH
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


def get_database_config() -> dict[str, Any]:
    cfg = get_analysis_config()
    node = cfg.get("database")
    return node if isinstance(node, dict) else {}


def get_postgres_dsn() -> str:
    db = get_database_config()
    pg = db.get("postgres") if isinstance(db.get("postgres"), dict) else {}
    return str(pg.get("dsn") or "").strip()


def get_accounts_config() -> dict[str, dict[str, Any]]:
    """多币种账户：键为大写币种代码（CNY/USD），值为 balance / max_loss_pct / qty_step 等。"""
    cfg = get_analysis_config()
    node = cfg.get("accounts")
    if not isinstance(node, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in node.items():
        ck = str(k).strip().upper()
        if isinstance(v, dict) and ck:
            out[ck] = dict(v)
    return out


def get_account_system_config() -> dict[str, Any]:
    """account_system 子树（无 enabled 开关；账本逻辑始终启用，无 PG 时内部 no-op）。"""
    cfg = get_analysis_config()
    node = cfg.get("account_system")
    return node if isinstance(node, dict) else {}


def get_account_initial_balance(currency: str) -> float:
    ac = get_accounts_config()
    c = str(currency).strip().upper()
    if c in ac:
        val = ac[c].get("initial_balance") or ac[c].get("balance")
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def reload_accounts_config() -> None:
    """Force reload of YAML config (accounts and other parameters).

    Call this after editing `config/analysis_defaults.yaml` during runtime.
    """
    global _CFG_CACHE
    _CFG_CACHE = _load_yaml(_resolve_cfg_path())

