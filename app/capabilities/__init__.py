"""统一能力层：market / research / sim_account 三域并列。

每个 capability 都返回 CapabilityResult，不关心调用方是谁。
"""
from app.capabilities.sim_account_capability import view_sim_account_state, SimAccountScope
from app.capabilities.market_capability import view_market_snapshot
from app.capabilities.research_capability import view_research_digest

__all__ = [
    "view_sim_account_state", "SimAccountScope",
    "view_market_snapshot",
    "view_research_digest",
]