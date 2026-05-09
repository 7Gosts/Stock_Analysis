from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.agent_service import TaskRunner


class AnalyzeRequest(BaseModel):
    symbol: str = Field(..., description="标的，例如 BTC_USDT")
    provider: str = Field(default="gateio")
    interval: str = Field(default="1d")
    limit: int = Field(default=180, ge=30, le=1000)
    with_research: bool = Field(default=False)
    research_n: int = Field(default=5, ge=1, le=50)
    research_type: str = Field(default="title")
    research_keyword: str | None = Field(default=None)
    analysis_style: str = Field(default="auto")
    question: str | None = Field(default=None, description="可选：触发RAG检索的问题")
    use_rag: bool = Field(default=True)
    rag_top_k: int = Field(default=5, ge=1, le=20)
    use_llm_decision: bool = Field(default=True, description="必须为 True：分析仅支持 LangGraph + DeepSeek")
    risk_profile: str | None = Field(default=None, description="可选：风险画像（如 保守/均衡/进取 或 单笔亏损阈值）")


class AnalyzeSubmitResponse(BaseModel):
    task_id: str
    status: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    created_at_utc: str
    updated_at_utc: str
    result: dict[str, Any] | None = None
    error: str | None = None


app = FastAPI(title="Stock Analysis Agent API", version="0.1.0")
runner = TaskRunner()
TASK_STORE: dict[str, dict[str, Any]] = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_task(task_id: str, req: AnalyzeRequest) -> None:
    row = TASK_STORE.get(task_id)
    if row is None:
        return
    row["status"] = "running"
    row["updated_at_utc"] = _utc_now_iso()
    try:
        result = runner.run_analysis(
            symbol=req.symbol,
            provider=req.provider,
            interval=req.interval,
            limit=req.limit,
            with_research=req.with_research,
            research_n=req.research_n,
            research_type=req.research_type,
            research_keyword=req.research_keyword,
            analysis_style=req.analysis_style,
            question=req.question,
            use_rag=req.use_rag,
            rag_top_k=req.rag_top_k,
            use_llm_decision=req.use_llm_decision,
        )
        if isinstance(result, dict) and req.risk_profile:
            meta = result.setdefault("meta", {})
            if isinstance(meta, dict):
                meta["risk_profile"] = req.risk_profile
        row["status"] = "completed"
        row["result"] = result
    except Exception as exc:
        row["status"] = "failed"
        row["error"] = str(exc)
    row["updated_at_utc"] = _utc_now_iso()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/agent/analyze", response_model=AnalyzeSubmitResponse)
def submit_analyze(req: AnalyzeRequest, background_tasks: BackgroundTasks) -> AnalyzeSubmitResponse:
    task_id = uuid4().hex
    now_iso = _utc_now_iso()
    TASK_STORE[task_id] = {
        "task_id": task_id,
        "status": "queued",
        "created_at_utc": now_iso,
        "updated_at_utc": now_iso,
        "result": None,
        "error": None,
    }
    background_tasks.add_task(_run_task, task_id, req)
    return AnalyzeSubmitResponse(task_id=task_id, status="queued")


@app.get("/agent/tasks/{task_id}", response_model=TaskStatusResponse)
def get_task(task_id: str) -> TaskStatusResponse:
    row = TASK_STORE.get(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    return TaskStatusResponse(**row)
