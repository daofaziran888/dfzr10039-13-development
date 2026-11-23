"""入口收件回执接口：批量收件、逐条追查、调度领取、执行登记。

固定路径必须早于通用资源路由注册，否则 ``/api/{module}/{entry_id}`` 会先匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app import entry_intake as intake
from app.store import store

router = APIRouter(prefix="/api/intake", tags=["入口收件回执"])


class AddressLinePayload(BaseModel):
    url: str
    line_no: int | None = None
    window_start: str | None = None
    window_end: str | None = None
    window_label: str | None = None


class ApplicationPayload(BaseModel):
    application_id: str | None = None
    job_id: str
    source: str
    job_kind: str = "日常采集"
    priority: int | str = 10
    window_start: str | None = None
    window_end: str | None = None
    window_label: str | None = None
    addresses: list[str | AddressLinePayload] = Field(default_factory=list)


class IntakePayload(BaseModel):
    applications: list[ApplicationPayload]
    submitted_at: str | None = None
    now: str | None = None
    receipt_id: str | None = None
    default_priority: int = 10


class ClaimPayload(BaseModel):
    receipt_id: str | None = None
    claim_ids: list[str] = Field(default_factory=list)
    claimed_by: str = "scheduler"
    now: str | None = None


class ExecutionPayload(BaseModel):
    application_id: str | None = None
    result: str = "成功"
    now: str | None = None


def _guard(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except intake.IntakeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/submit", response_model=dict)
def submit(payload: IntakePayload) -> dict[str, Any]:
    """一次提交多份申请，返回可逐条追查的收件回执。"""
    return _guard(
        intake.submit_intake,
        [item.model_dump() for item in payload.applications],
        submitted_at=payload.submitted_at,
        now=payload.now,
        receipt_id=payload.receipt_id,
        default_priority=payload.default_priority,
    )


@router.get("/receipts", response_model=list[dict])
def list_receipts() -> list[dict[str, Any]]:
    return intake.list_receipts()


@router.get("/receipts/{receipt_id}", response_model=dict)
def get_receipt(receipt_id: str) -> dict[str, Any]:
    receipt = intake.get_receipt(receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail=f"收件回执 {receipt_id} 不存在")
    return receipt


@router.get("/receipts/{receipt_id}/applications/{application_id}", response_model=dict)
def get_application(receipt_id: str, application_id: str) -> dict[str, Any]:
    detail = intake.query_application(receipt_id, application_id)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"回执 {receipt_id} 下没有申请 {application_id}",
        )
    return detail


@router.get("/claims", response_model=list[dict])
def list_claims(
    receipt_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    job_id: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    return intake.list_claims(receipt_id=receipt_id, status=status, job_id=job_id)


@router.post("/claims/claim", response_model=dict)
def claim(payload: ClaimPayload) -> dict[str, Any]:
    """调度从入口收件回执认领真正可执行的地址。"""
    return _guard(
        intake.claim_executable,
        receipt_id=payload.receipt_id,
        claim_ids=payload.claim_ids,
        claimed_by=payload.claimed_by,
        now=payload.now,
    )


@router.post("/claims/{claim_id}/execution", response_model=dict)
def execute(claim_id: str, payload: ExecutionPayload) -> dict[str, Any]:
    """执行后登记入口实际由哪份申请提供。"""
    return _guard(
        intake.record_execution,
        claim_id,
        application_id=payload.application_id,
        result=payload.result,
        now=payload.now,
    )


@router.get("/jobs/{job_id}", response_model=dict)
def job_receipts(job_id: str) -> dict[str, Any]:
    """按采集作业汇总收件、认领和实际执行收到的地址。"""
    returned = [
        intake.serialize_claim(row) for row in store.rows(intake.CLAIM_TABLE)
        if str(row.get("来源作业")) == job_id
    ]
    linked = [
        intake.serialize_line(row) for row in store.rows(intake.LINE_TABLE)
        if str(row.get("来源作业")) == job_id and row.get("status") == intake.ITEM_LINKED
    ]
    executed = [claim for claim in returned if claim["认领状态"] == "已执行"]
    rejected = [
        intake.serialize_line(row) for row in store.rows(intake.LINE_TABLE)
        if str(row.get("来源作业")) == job_id and row.get("status") == intake.ITEM_REJECTED
    ]
    return {
        "来源作业": job_id,
        "申请": [
            intake.serialize_application(row) for row in store.rows(intake.INTAKE_TABLE)
            if str(row.get("来源作业")) == job_id
        ],
        "实收地址": [claim["入口地址"] for claim in executed],
        "执行登记": [
            {
                "认领编号": claim["认领编号"],
                "入口地址": claim["入口地址"],
                "提供申请": claim["提供申请"],
                "执行登记": claim["执行登记"],
                "执行结果": claim["执行结果"],
            }
            for claim in executed
        ],
        "认领列表": returned,
        "归属他人地址": linked,
        "退回地址": rejected,
    }
