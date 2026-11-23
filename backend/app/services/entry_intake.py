"""入口收件回执领域服务：一次批量收件，按申请与地址行逐条追查。

收件、认领与执行登记的业务判断都在 ``app.entry_intake``，这里只负责把列表能力
接入统一的资源路由，让「入口收件回执」在列表里可以按回执、申请、作业筛选。
"""
from __future__ import annotations

from app.services._intake_guard import IntakeReadOnlyMixin


class EntryIntakeService(IntakeReadOnlyMixin):
    MODULE = "entry_intake"
    ENTITY = "入口收件申请"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["申请编号", "来源作业", "申请类型", "合法来源", "分配优先级",
                   "活动窗口", "地址条数", "可执行地址数", "归属地址数", "退回地址数",
                   "归属回执", "提交时间"]
    REQUIRED_FIELDS = ["申请编号", "来源作业", "合法来源"]
    STATUS_ORDER = ["待确认", "已确认", "部分退回", "全部退回"]
    ACTION_RULES = {}
    NEGATIVE_ACTIONS = []
