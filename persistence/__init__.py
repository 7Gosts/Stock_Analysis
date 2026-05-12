"""PostgreSQL 持久化层：连接池、SQL 文件加载、台账仓库、账户与纸交易写入。

编排与 HTTP 仍在 `app/`；纯数据访问与 SQL 绑定集中在本包。"""
from __future__ import annotations
