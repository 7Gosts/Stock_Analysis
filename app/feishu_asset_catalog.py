"""飞书机器人：从 market_config.json 构建可交易标的索引（symbol → provider / 研报关键词）。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


def _market_to_provider(market: str) -> str:
    m = (market or "").strip().upper()
    if m == "CRYPTO":
        return "gateio"
    if m == "PM":
        return "goldapi"
    return "tickflow"


@dataclass(frozen=True)
class FeishuAssetCatalog:
    """by_symbol 的 key 为 symbol.upper()。"""

    by_symbol: dict[str, dict[str, Any]]
    config_path: Path

    def get(self, symbol_upper: str) -> dict[str, Any] | None:
        return self.by_symbol.get(symbol_upper.strip().upper())

    def provider_for(self, symbol_upper: str) -> str | None:
        row = self.get(symbol_upper)
        if not row:
            return None
        return str(row.get("provider") or "").strip().lower() or None

    def research_keyword_for(self, symbol_upper: str) -> str | None:
        row = self.get(symbol_upper)
        if not row:
            return None
        kw = row.get("research_keyword")
        if isinstance(kw, str) and kw.strip():
            return kw.strip()
        name = row.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return None

    def tradable_assets_for_prompt(self) -> list[dict[str, Any]]:
        """供路由 LLM 的精简列表（稳定排序）。"""
        rows = list(self.by_symbol.values())
        rows.sort(key=lambda x: str(x.get("symbol") or ""))
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "symbol": r.get("symbol"),
                    "market": r.get("market"),
                    "provider": r.get("provider"),
                    "name": r.get("name"),
                }
            )
        return out

    @property
    def allowed_symbols(self) -> frozenset[str]:
        return frozenset(self.by_symbol.keys())


def _load_market_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_feishu_asset_catalog(*, config_path: Path | None = None) -> FeishuAssetCatalog:
    root = Path(__file__).resolve().parents[1]
    path = (config_path or (root / "config" / "market_config.json")).resolve()
    obj = _load_market_config(path)
    assets = obj.get("assets")
    by_symbol: dict[str, dict[str, Any]] = {}
    if isinstance(assets, list):
        for a in assets:
            if not isinstance(a, dict):
                continue
            sym = str(a.get("symbol") or "").strip().upper()
            if not sym:
                continue
            market = str(a.get("market") or "").strip().upper() or "US"
            provider = _market_to_provider(market)
            by_symbol[sym] = {
                "symbol": sym,
                "market": market,
                "provider": provider,
                "name": str(a.get("name") or sym).strip() or sym,
                "research_keyword": (
                    str(a.get("research_keyword")).strip()
                    if isinstance(a.get("research_keyword"), str) and str(a.get("research_keyword")).strip()
                    else None
                ),
            }
    return FeishuAssetCatalog(by_symbol=by_symbol, config_path=path)


@lru_cache(maxsize=4)
def get_feishu_asset_catalog_cached(config_path_str: str) -> FeishuAssetCatalog:
    return load_feishu_asset_catalog(config_path=Path(config_path_str))


def get_catalog_for_repo(repo_root: Path | None = None) -> FeishuAssetCatalog:
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    return get_feishu_asset_catalog_cached(str(root / "config" / "market_config.json"))


# 裸代码 → 完整交易对（仅当 catalog 中存在该对时落地）
_BARE_CRYPTO: dict[str, str] = {
    "BTC": "BTC_USDT",
    "ETH": "ETH_USDT",
    "SOL": "SOL_USDT",
}


def canonical_tradable_symbol(raw: str, catalog: FeishuAssetCatalog) -> str | None:
    v = (raw or "").strip().upper()
    if not v:
        return None
    if v in catalog.by_symbol:
        return v
    mapped = _BARE_CRYPTO.get(v)
    if mapped and mapped in catalog.by_symbol:
        return mapped
    return None


def canonical_tradable_symbol_list(values: Any, catalog: FeishuAssetCatalog) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for it in values:
        c = canonical_tradable_symbol(str(it or ""), catalog)
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def normalize_provider(value: str | None, *, symbol_upper: str, catalog: FeishuAssetCatalog) -> str:
    """校验 LLM 给出的 provider；非法或与标的表不一致时以 catalog 为准。"""
    expected = catalog.provider_for(symbol_upper) or "tickflow"
    p = str(value or "").strip().lower()
    if p not in {"tickflow", "gateio", "goldapi"}:
        return expected
    if p != expected:
        return expected
    return p
