"""入口收件四个只读模块共用的写入拦截：只允许通过批量收件接口产生记录。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService


class IntakeReadOnlyMixin(BaseModuleService):
    SUBMIT_ENDPOINT = "/api/intake/submit"

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        return None, ["批量收件"]

    def missing_message(self, missing: list[str]) -> str:
        return (
            f"{self.ENTITY}只能通过批量收件接口 {self.SUBMIT_ENDPOINT} 产生，"
            "请一次提交申请及其地址行")

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        return None, (
            "入口收件流转请使用调度领取与执行登记接口，"
            f"不能直接对{self.ENTITY}执行「{action}」")
