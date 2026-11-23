"""回执逐条明细领域服务：只读展示每份申请里每个地址的处置与归属。"""
from __future__ import annotations

from app.services._intake_guard import IntakeReadOnlyMixin


class EntryIntakeLineService(IntakeReadOnlyMixin):
    MODULE = "entry_intake_line"
    ENTITY = "回执地址行"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["收件回执", "申请编号", "来源作业", "行号", "原地址", "地址指纹",
                   "活动窗口", "处置结果", "归属申请", "归属认领", "退回缘由"]
    REQUIRED_FIELDS = ["申请编号", "原地址"]
    STATUS_ORDER = ["可认领", "已领取", "已执行", "沿用认领", "已归属", "已退回"]
    ACTION_RULES = {}
    NEGATIVE_ACTIONS = []
