"""调度认领领域服务：展示从收件回执同步过来的可执行地址及领取、执行状态。"""
from __future__ import annotations

from app.services._intake_guard import IntakeReadOnlyMixin


class EntryClaimService(IntakeReadOnlyMixin):
    MODULE = "entry_claim"
    ENTITY = "入口认领"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["认领编号", "收件回执", "申请编号", "来源作业", "入口地址",
                   "地址指纹", "原行位置", "活动窗口", "提供申请", "执行登记",
                   "领取人", "执行时间"]
    REQUIRED_FIELDS = ["认领编号", "申请编号", "入口地址"]
    STATUS_ORDER = ["待领取", "已领取", "已执行", "已取消"]
    ACTION_RULES = {}
    NEGATIVE_ACTIONS = []
