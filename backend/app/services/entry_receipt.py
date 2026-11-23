"""收件回执领域服务：回执摘要列表，完整回执走 /api/entry_intake/receipts/{id}。"""
from __future__ import annotations

from app.services._intake_guard import IntakeReadOnlyMixin


class EntryReceiptService(IntakeReadOnlyMixin):
    MODULE = "entry_receipt"
    ENTITY = "入口收件回执"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["回执编号", "提交时间", "申请数", "地址行数", "可执行地址数",
                   "归属地址数", "退回地址数", "登记时间"]
    REQUIRED_FIELDS = ["回执编号"]
    STATUS_ORDER = ["已确认"]
    ACTION_RULES = {}
    NEGATIVE_ACTIONS = []
