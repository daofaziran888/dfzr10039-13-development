"""入口收件回执与调度认领：一次批量收件、逐条追查、指纹判等、同窗口归属。

设计要点：

- ``submit_intake`` 一次可以收下多份申请；回执按申请、按地址行逐条留痕，随时可查。
- 地址等价性沿用 ``url_dedup`` 的既有指纹算法：查询参数次序不同视为同一地址。
- 每份申请、每个地址行都保留原地址、原行位置和活动窗口；行窗口优先于申请窗口。
- 合法地址按「已有有效认领优先 → 分配优先级数值大者优先 → 同优先级先到先得」取得采集
  凭证；同一指纹在重叠窗口内只允许存在一份有效认领，其余申请记为「已归属」并指向实际
  归属的认领，不做第二次认领。
- 坏地址、时段已错过 / 未开始的地址只退回该行，不牵连同一申请的其它地址和其它作业。
- 收件结果同步进调度认领列表（``entry_claim``，初始「待领取」）；调度显式领取可执行地址，
  执行后登记实际提供入口的申请。已存在的有效认领不会因为原申请重送或改优先级而换归属。
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

from app.models import EntryApplication, UrlFingerprint
from app.store import store


INTAKE_TABLE = "entry_intake"        # 收件申请（列表视图）
LINE_TABLE = "entry_intake_line"     # 申请内地址行（逐条回执）
RECEIPT_TABLE = "entry_receipt"      # 收件回执摘要
CLAIM_TABLE = "entry_claim"          # 调度认领列表

APPLICATION_STATUSES = ["待确认", "已确认", "部分退回", "全部退回"]
CLAIM_STATUSES = ["待领取", "已领取", "已执行", "已取消"]

# 行状态：可认领 / 已领取 / 已执行 / 沿用既有认领 / 已归属（他人）/ 已退回
ITEM_PENDING = "可认领"
ITEM_CLAIMED = "已领取"
ITEM_EXECUTED = "已执行"
ITEM_REUSED = "沿用认领"
ITEM_LINKED = "已归属"
ITEM_REJECTED = "已退回"

EXECUTABLE_STATUSES = {ITEM_PENDING, ITEM_CLAIMED, ITEM_EXECUTED, ITEM_REUSED}
BLOCKING_CLAIM_STATUSES = {"待领取", "已领取", "已执行"}

_PRIORITY_WORDS = {"高": 30, "中": 20, "低": 10}

_LOCK = threading.RLock()
_SEQ = {"value": 0}


class IntakeError(ValueError):
    """收件参数或状态流转不合法，由接口层转换为 4xx。"""


# ---------------------------------------------------------------------------
# 时间与标识
# ---------------------------------------------------------------------------

def _now(now: datetime | str | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    parsed = _parse_time(now)
    if parsed is None:
        raise IntakeError(f"无法解析时间：{now}")
    return parsed


def _parse_time(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(normalized, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _next_seq() -> int:
    _SEQ["value"] += 1
    return _SEQ["value"]


def _next_row_id(table: str) -> int:
    rows = store.rows(table)
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _new_id(prefix: str, table: str, id_field: str, moment: datetime) -> str:
    existing = {str(row.get(id_field, "")) for row in store.rows(table)}
    base = f"{prefix}-{moment.strftime('%Y%m%d')}"
    for index in range(1, 100000):
        candidate = f"{base}-{index:04d}"
        if candidate not in existing:
            return candidate
    raise IntakeError(f"{prefix} 编号空间已耗尽")


def _window_text(start: str, end: str) -> str:
    if not start and not end:
        return "长期有效"
    return f"{start or '不限'} ~ {end or '不限'}"


# ---------------------------------------------------------------------------
# 地址校验与指纹
# ---------------------------------------------------------------------------

def normalize_url(url: str) -> str:
    """结构校验并返回去掉首尾空白的地址；非法地址抛 IntakeError。"""
    text = str(url or "").strip()
    if not text:
        raise IntakeError("地址为空")
    if any(ch.isspace() for ch in text):
        raise IntakeError("地址含空白字符")
    parsed = urlparse(text)
    if parsed.scheme.lower() not in ("http", "https"):
        raise IntakeError("地址必须以 http:// 或 https:// 开头")
    if not parsed.netloc or not parsed.hostname:
        raise IntakeError("地址缺少主机名")
    try:
        parsed.port
    except ValueError:
        raise IntakeError("地址端口非法")
    return text


def address_fingerprint(url: str) -> str:
    """按既有指纹算法计算地址指纹；查询参数次序不同得到同一指纹。"""
    return UrlFingerprint.compute(normalize_url(url)).hash_value


def _register_fingerprint(url: str, channel: str = "entry_intake") -> tuple[str, bool]:
    """登记或复用 url_dedup 中的指纹，返回 (指纹哈希, 是否已有指纹)。"""
    normalized = normalize_url(url)
    fingerprint = UrlFingerprint.compute(normalized)
    for row in store.rows("url_dedup"):
        if str(row.get("指纹哈希", "")) == fingerprint.hash_value:
            try:
                row["重复次数"] = int(row.get("重复次数", 0)) + 1
            except (TypeError, ValueError):
                row["重复次数"] = 1
            return fingerprint.hash_value, True

    store.rows("url_dedup").append({
        "id": _next_row_id("url_dedup"),
        "指纹编号": fingerprint.fingerprint_id,
        "源URL": normalized,
        "指纹算法": fingerprint.algorithm,
        "指纹哈希": fingerprint.hash_value,
        "发现渠道": channel,
        "首次发现": fingerprint.first_seen,
        "重复次数": 0,
        "去重状态": "已记录",
        "status": "已记录",
        "pending": False,
        "abnormal": False,
    })
    return fingerprint.hash_value, False


# ---------------------------------------------------------------------------
# 入参规范化
# ---------------------------------------------------------------------------

def _normalize_priority(value: Any, application_id: str) -> int:
    if isinstance(value, str) and value.strip() in _PRIORITY_WORDS:
        return _PRIORITY_WORDS[value.strip()]
    try:
        return int(value)
    except (TypeError, ValueError):
        raise IntakeError(f"申请 {application_id} 的分配优先级必须是整数或 高/中/低")


def _normalize_application(raw: Any, index: int, submitted_at: str,
                           fallback_priority: int) -> dict[str, Any]:
    app = EntryApplication.from_payload(raw, index, submitted_at)
    payload = app.to_dict()
    payload["priority"] = _normalize_priority(
        payload["priority"] if payload["priority"] is not None else fallback_priority,
        app.application_id)
    return payload


# ---------------------------------------------------------------------------
# 窗口
# ---------------------------------------------------------------------------

def _parse_window(start: str, end: str, owner: str) -> tuple[datetime | None, datetime | None]:
    start_dt = _parse_time(start)
    end_dt = _parse_time(end)
    if str(start or "").strip() and start_dt is None:
        raise IntakeError(f"{owner} 的活动窗口起时间无法解析：{start}")
    if str(end or "").strip() and end_dt is None:
        raise IntakeError(f"{owner} 的活动窗口止时间无法解析：{end}")
    if start_dt and end_dt and end_dt < start_dt:
        raise IntakeError(f"{owner} 的活动窗口止时间早于起时间")
    return start_dt, end_dt


def _window_state(start_dt: datetime | None, end_dt: datetime | None, now: datetime) -> str:
    if end_dt and now >= end_dt:
        return "missed"
    if start_dt and now < start_dt:
        return "early"
    return "active"


def _overlaps(a_start: datetime | None, a_end: datetime | None,
              b_start: datetime | None, b_end: datetime | None) -> bool:
    """半开区间相交；None 表示该侧不设限。"""
    if a_end and b_start and a_end <= b_start:
        return False
    if b_end and a_start and b_end <= a_start:
        return False
    return True


# ---------------------------------------------------------------------------
# 批量收件主流程
# ---------------------------------------------------------------------------

def submit_intake(
    applications: Iterable[Any] | dict[str, Any],
    *,
    submitted_at: datetime | str | None = None,
    now: datetime | str | None = None,
    receipt_id: str | None = None,
    default_priority: int = 10,
) -> dict[str, Any]:
    """一次提交多份申请，返回可逐条追查的收件回执。

    处理顺序：地址与窗口校验 → 指纹分组 → 同指纹重叠窗口内定归属。单行失败只退回该行。
    整个过程持锁，多个申请（含并发收件）对同一地址只会有一次有效认领。
    """
    if isinstance(applications, dict):
        applications = applications.get("applications", applications.get("申请列表", [applications]))
    with _LOCK:
        moment = _now(now)
        stamp = _iso(_now(submitted_at)) if submitted_at is not None else _iso(moment)
        normalized = [
            _normalize_application(raw, index, stamp, default_priority)
            for index, raw in enumerate(applications, start=1)
        ]
        if not normalized:
            raise IntakeError("批量收件至少需要一份申请")

        rid = str(receipt_id or "").strip() or _new_id("RCPT", RECEIPT_TABLE, "回执编号", moment)
        if any(str(row.get("回执编号")) == rid for row in store.rows(RECEIPT_TABLE)):
            raise IntakeError(f"回执编号已存在：{rid}")

        seen: set[str] = set()
        for app in normalized:
            if app["application_id"] in seen:
                raise IntakeError(f"同一次收件内申请编号重复：{app['application_id']}")
            seen.add(app["application_id"])

        app_rows: list[dict[str, Any]] = []
        line_rows: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []

        for order, app in enumerate(normalized, start=1):
            seq = _next_seq()
            try:
                app_start, app_end = _parse_window(
                    app["window_start"], app["window_end"], f"申请 {app['application_id']}")
            except IntakeError as exc:
                app_row = _persist_application(app, rid, order, seq, stamp, None, None)
                app_row.update({"status": "全部退回", "pending": False, "abnormal": True,
                                "退回缘由": str(exc)})
                app_rows.append(app_row)
                for line in app["addresses"] or [{"url": "", "line_no": 0, "window_label": ""}]:
                    line_rows.append(_persist_line(
                        app, line, rid, stamp, None, None,
                        status=ITEM_REJECTED, disposition="申请窗口非法退回",
                        reason=str(exc), fingerprint=""))
                continue

            app_state = _window_state(app_start, app_end, moment)
            app_row = _persist_application(app, rid, order, seq, stamp, app_start, app_end)
            app_rows.append(app_row)

            rejection = _application_rejection(app, app_state)
            if rejection:
                app_row.update({"status": "全部退回", "pending": False, "abnormal": True,
                                "退回缘由": rejection})
                for line in app["addresses"]:
                    line_rows.append(_persist_line(
                        app, line, rid, stamp, app_start, app_end,
                        status=ITEM_REJECTED, disposition="申请级退回",
                        reason=rejection, fingerprint=""))
                continue

            for line in app["addresses"]:
                line_row, candidate = _evaluate_line(
                    app, line, rid, stamp, order, seq, app_start, app_end, moment)
                line_rows.append(line_row)
                if candidate is not None:
                    candidates.append(candidate)

        _resolve_claims(candidates)
        _finalize_applications(app_rows, line_rows)

        receipt_row = _persist_receipt(rid, stamp, app_rows, line_rows, moment)
        return _build_receipt(receipt_row)


def _application_rejection(app: dict[str, Any], state: str) -> str:
    if not app["job_id"]:
        return "缺少来源作业编号"
    if not app["source"]:
        return "缺少合法来源"
    if not app["addresses"]:
        return "申请未携带入口地址"
    if state == "missed":
        return "申请活动窗口已错过"
    if state == "early":
        return "申请活动窗口尚未开始"
    return ""


def _evaluate_line(app: dict[str, Any], line: dict[str, Any], rid: str, stamp: str,
                   order: int, seq: int, app_start: datetime | None,
                   app_end: datetime | None, moment: datetime
                   ) -> tuple[dict[str, Any], dict[str, Any] | None]:
    start_text = str(line.get("window_start") or app["window_start"] or "")
    end_text = str(line.get("window_end") or app["window_end"] or "")
    owner = f"申请 {app['application_id']} 第 {line['line_no']} 行"
    try:
        line_start, line_end = _parse_window(start_text, end_text, owner)
    except IntakeError as exc:
        row = _persist_line(app, line, rid, stamp, None, None, status=ITEM_REJECTED,
                            disposition="窗口非法退回", reason=str(exc), fingerprint="")
        return row, None

    try:
        normalize_url(line["url"])
    except IntakeError as exc:
        row = _persist_line(app, line, rid, stamp, line_start, line_end,
                            status=ITEM_REJECTED, disposition="坏地址退回",
                            reason=f"地址不合法：{exc}", fingerprint="")
        return row, None

    fingerprint = address_fingerprint(line["url"])
    state = _window_state(line_start, line_end, moment)
    if state == "missed":
        row = _persist_line(
            app, line, rid, stamp, line_start, line_end, status=ITEM_REJECTED,
            disposition="时段已错过退回", fingerprint=fingerprint,
            reason="要求的时段已错过，本条不参与认领")
        return row, None
    if state == "early":
        row = _persist_line(
            app, line, rid, stamp, line_start, line_end, status=ITEM_REJECTED,
            disposition="时段未到退回", fingerprint=fingerprint,
            reason="要求的时段尚未开始，本条暂不参与认领")
        return row, None

    registered, existed = _register_fingerprint(line["url"])
    row = _persist_line(app, line, rid, stamp, line_start, line_end, status=ITEM_PENDING,
                        disposition="待确认归属", reason="", fingerprint=registered)
    candidate = {
        "row": row,
        "fingerprint": registered,
        "existed": existed,
        "priority": app["priority"],
        "seq": seq,
        "order": order,
        "start": line_start,
        "end": line_end,
    }
    return row, candidate


def _resolve_claims(candidates: list[dict[str, Any]]) -> None:
    """同一指纹按优先级定归属；已有有效认领优先，且只允许一份有效认领。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate["fingerprint"], []).append(candidate)

    for fingerprint, group in grouped.items():
        group.sort(key=lambda item: (-int(item["priority"]), int(item["seq"])))
        existing = _active_claims(fingerprint)
        for candidate in group:
            row = candidate["row"]
            conflict = _find_conflict(existing, candidate["start"], candidate["end"])
            if conflict is None:
                claim = _create_claim(candidate)
                existing.append(claim)
                row.update({
                    "status": ITEM_PENDING,
                    "pending": True,
                    "abnormal": False,
                    "处置结果": "取得采集凭证",
                    "归属申请": row["申请编号"],
                    "归属认领": claim["认领编号"],
                    "认领编号": claim["认领编号"],
                    "退回缘由": "",
                })
                continue

            same_application = (
                str(conflict.get("申请编号", "")) == row["申请编号"]
                and str(conflict.get("来源作业", "")) == row["来源作业"])
            if same_application:
                row.update({
                    "status": ITEM_REUSED,
                    "pending": False,
                    "abnormal": False,
                    "处置结果": "沿用既有认领",
                    "归属申请": row["申请编号"],
                    "归属认领": conflict["认领编号"],
                    "认领编号": conflict["认领编号"],
                    "退回缘由": "本申请此前已取得该地址的认领，重送沿用原认领",
                })
                continue

            row.update({
                "status": ITEM_LINKED,
                "pending": False,
                "abnormal": False,
                "处置结果": "关联到实际归属",
                "归属申请": conflict.get("申请编号", ""),
                "归属认领": conflict.get("认领编号", ""),
                "认领编号": "",
                "退回缘由": (
                    f"同一地址在重叠活动窗口内已有归属：申请 {conflict.get('申请编号')}"
                    f"（认领 {conflict.get('认领编号')}），不可重复认领"),
            })
            _append_related_application(conflict, row)


def _active_claims(fingerprint: str) -> list[dict[str, Any]]:
    return [
        row for row in store.rows(CLAIM_TABLE)
        if str(row.get("地址指纹", "")) == fingerprint
        and str(row.get("认领状态", row.get("status", ""))) in BLOCKING_CLAIM_STATUSES
    ]


def _find_conflict(existing: list[dict[str, Any]], start: datetime | None,
                   end: datetime | None) -> dict[str, Any] | None:
    for claim in existing:
        claim_start = _parse_time(str(claim.get("窗口起", "")))
        claim_end = _parse_time(str(claim.get("窗口止", "")))
        if _overlaps(start, end, claim_start, claim_end):
            return claim
    return None


def _append_related_application(claim: dict[str, Any], row: dict[str, Any]) -> None:
    related = claim.setdefault("关联申请", [])
    entry = {
        "申请编号": row["申请编号"],
        "来源作业": row["来源作业"],
        "原行位置": row["原行位置"],
    }
    if entry not in related:
        related.append(entry)


def _create_claim(candidate: dict[str, Any]) -> dict[str, Any]:
    row = candidate["row"]
    moment = _parse_time(row.get("提交时间")) or datetime.now(timezone.utc)
    claim = {
        "id": _next_row_id(CLAIM_TABLE),
        "status": "待领取",
        "pending": True,
        "abnormal": False,
        "认领编号": _new_id("CLM", CLAIM_TABLE, "认领编号", moment),
        "收件回执": row["收件回执"],
        "申请编号": row["申请编号"],
        "来源作业": row["来源作业"],
        "入口地址": row["原地址"],
        "地址指纹": row["地址指纹"],
        "原行位置": row["原行位置"],
        "窗口起": row["窗口起"],
        "窗口止": row["窗口止"],
        "活动窗口": row["活动窗口"],
        "分配优先级": candidate["priority"],
        "认领状态": "待领取",
        "提供申请": row["申请编号"],
        "执行登记": "",
        "领取人": "",
        "领取时间": "",
        "执行时间": "",
        "执行结果": "",
        "关联申请": [],
    }
    store.rows(CLAIM_TABLE).append(claim)
    return claim


def _finalize_applications(app_rows: list[dict[str, Any]],
                           line_rows: list[dict[str, Any]]) -> None:
    by_app: dict[str, list[dict[str, Any]]] = {}
    for row in line_rows:
        by_app.setdefault(row["申请编号"], []).append(row)
    for app_row in app_rows:
        if app_row.get("status") == "全部退回":
            continue
        lines = by_app.get(app_row["申请编号"], [])
        rejected = [line for line in lines if line["status"] == ITEM_REJECTED]
        linked = [line for line in lines if line["status"] == ITEM_LINKED]
        executable = [line for line in lines if line["status"] in EXECUTABLE_STATUSES]
        summary = _summarize_lines(lines)
        if not executable:
            app_row.update({
                "status": "全部退回",
                "pending": False,
                "abnormal": True,
                "可执行地址数": 0,
                "归属地址数": len(linked),
                "退回地址数": len(rejected),
                "退回缘由": summary,
            })
        elif rejected or linked:
            app_row.update({
                "status": "部分退回",
                "pending": True,
                "abnormal": False,
                "可执行地址数": len(executable),
                "归属地址数": len(linked),
                "退回地址数": len(rejected),
                "退回缘由": summary,
            })
        else:
            app_row.update({
                "status": "已确认",
                "pending": True,
                "abnormal": False,
                "可执行地址数": len(executable),
                "归属地址数": 0,
                "退回地址数": 0,
                "退回缘由": "",
            })


def _summarize_lines(lines: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for line in lines:
        if line["status"] == ITEM_REJECTED:
            parts.append(f"第{line['行号']}行{line['处置结果']}")
        elif line["status"] == ITEM_LINKED:
            parts.append(f"第{line['行号']}行归属申请{line['归属申请']}")
        elif line["status"] == ITEM_REUSED:
            parts.append(f"第{line['行号']}行沿用认领")
    return "；".join(parts)


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------

def _persist_application(app: dict[str, Any], rid: str, order: int, seq: int,
                         stamp: str, start: datetime | None,
                         end: datetime | None) -> dict[str, Any]:
    start_text = _iso(start) if start else ""
    end_text = _iso(end) if end else ""
    row = {
        "id": _next_row_id(INTAKE_TABLE),
        "status": "待确认",
        "pending": True,
        "abnormal": False,
        "申请编号": app["application_id"],
        "来源作业": app["job_id"],
        "申请类型": app["job_kind"],
        "合法来源": app["source"],
        "分配优先级": app["priority"],
        "活动窗口": app["window_label"] or _window_text(start_text, end_text),
        "窗口起": start_text,
        "窗口止": end_text,
        "地址条数": len(app["addresses"]),
        "提交顺序": order,
        "提交序号": seq,
        "归属回执": rid,
        "提交时间": stamp,
        "可执行地址数": 0,
        "归属地址数": 0,
        "退回地址数": 0,
        "退回缘由": "",
    }
    store.rows(INTAKE_TABLE).append(row)
    return row


def _persist_line(app: dict[str, Any], line: dict[str, Any], rid: str, stamp: str,
                  start: datetime | None, end: datetime | None, *,
                  status: str, disposition: str, reason: str,
                  fingerprint: str) -> dict[str, Any]:
    start_text = _iso(start) if start else ""
    end_text = _iso(end) if end else ""
    row = {
        "id": _next_row_id(LINE_TABLE),
        "status": status,
        "pending": status in (ITEM_PENDING, ITEM_CLAIMED),
        "abnormal": status == ITEM_REJECTED,
        "收件回执": rid,
        "申请编号": app["application_id"],
        "来源作业": app["job_id"],
        "申请类型": app["job_kind"],
        "合法来源": app["source"],
        "分配优先级": app["priority"],
        "行号": line.get("line_no", 0),
        "原行位置": f"{app['application_id']}#{line.get('line_no', 0)}",
        "原地址": str(line.get("url") or ""),
        "地址指纹": fingerprint,
        "活动窗口": line.get("window_label") or _window_text(start_text, end_text),
        "窗口起": start_text,
        "窗口止": end_text,
        "处置结果": disposition,
        "归属申请": "",
        "归属认领": "",
        "认领编号": "",
        "执行申请": "",
        "执行时间": "",
        "退回缘由": reason,
        "提交时间": stamp,
    }
    store.rows(LINE_TABLE).append(row)
    return row


def _persist_receipt(rid: str, stamp: str, app_rows: list[dict[str, Any]],
                     line_rows: list[dict[str, Any]], moment: datetime) -> dict[str, Any]:
    def count(predicate) -> int:
        return sum(1 for line in line_rows if predicate(line))

    row = {
        "id": _next_row_id(RECEIPT_TABLE),
        "status": "已确认",
        "pending": any(line["status"] == ITEM_PENDING for line in line_rows),
        "abnormal": any(line["status"] == ITEM_REJECTED for line in line_rows),
        "回执编号": rid,
        "提交时间": stamp,
        "申请数": len(app_rows),
        "地址行数": len(line_rows),
        "可执行地址数": count(lambda line: line["status"] in EXECUTABLE_STATUSES),
        "归属地址数": count(lambda line: line["status"] == ITEM_LINKED),
        "退回地址数": count(lambda line: line["status"] == ITEM_REJECTED),
        "新建认领数": sum(1 for claim in store.rows(CLAIM_TABLE)
                      if claim.get("收件回执") == rid),
        "登记时间": _iso(moment),
    }
    store.rows(RECEIPT_TABLE).append(row)
    return row


# ---------------------------------------------------------------------------
# 回执视图
# ---------------------------------------------------------------------------

def serialize_line(row: dict[str, Any]) -> dict[str, Any]:
    """把回执行记录转成对外结构（供路由与工作台共用）。"""
    return _public_line(row)


def serialize_claim(row: dict[str, Any]) -> dict[str, Any]:
    """把认领记录转成对外结构（供路由与工作台共用）。"""
    return _public_claim(row)


def serialize_application(row: dict[str, Any]) -> dict[str, Any]:
    """把收件申请记录转成对外结构。"""
    return _public_application(row)


def _public_line(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "申请编号": row.get("申请编号", ""),
        "来源作业": row.get("来源作业", ""),
        "申请类型": row.get("申请类型", ""),
        "分配优先级": row.get("分配优先级", 0),
        "行号": row.get("行号", 0),
        "原行位置": row.get("原行位置", ""),
        "原地址": row.get("原地址", ""),
        "地址指纹": row.get("地址指纹", ""),
        "活动窗口": row.get("活动窗口", ""),
        "行状态": row.get("status", ""),
        "处置结果": row.get("处置结果", ""),
        "归属申请": row.get("归属申请", ""),
        "归属认领": row.get("归属认领", ""),
        "认领编号": row.get("认领编号", ""),
        "执行申请": row.get("执行申请", ""),
        "退回缘由": row.get("退回缘由", ""),
    }


def _public_claim(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "认领编号": row.get("认领编号", ""),
        "收件回执": row.get("收件回执", ""),
        "申请编号": row.get("申请编号", ""),
        "来源作业": row.get("来源作业", ""),
        "入口地址": row.get("入口地址", ""),
        "地址指纹": row.get("地址指纹", ""),
        "原行位置": row.get("原行位置", ""),
        "活动窗口": row.get("活动窗口", ""),
        "分配优先级": row.get("分配优先级", 0),
        "认领状态": row.get("认领状态", ""),
        "提供申请": row.get("提供申请", ""),
        "执行登记": row.get("执行登记", ""),
        "领取人": row.get("领取人", ""),
        "领取时间": row.get("领取时间", ""),
        "执行时间": row.get("执行时间", ""),
        "执行结果": row.get("执行结果", ""),
        "关联申请": [dict(item) for item in row.get("关联申请", [])],
    }


def _public_application(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "申请编号": row.get("申请编号", ""),
        "来源作业": row.get("来源作业", ""),
        "申请类型": row.get("申请类型", ""),
        "合法来源": row.get("合法来源", ""),
        "分配优先级": row.get("分配优先级", 0),
        "活动窗口": row.get("活动窗口", ""),
        "提交顺序": row.get("提交顺序", 0),
        "状态": row.get("status", ""),
        "地址条数": row.get("地址条数", 0),
        "可执行地址数": row.get("可执行地址数", 0),
        "归属地址数": row.get("归属地址数", 0),
        "退回地址数": row.get("退回地址数", 0),
        "退回缘由": row.get("退回缘由", ""),
    }


def _build_receipt(receipt_row: dict[str, Any]) -> dict[str, Any]:
    rid = receipt_row["回执编号"]
    app_rows = [row for row in store.rows(INTAKE_TABLE) if row.get("归属回执") == rid]
    app_rows.sort(key=lambda row: int(row.get("提交顺序", 0)))
    line_rows = [row for row in store.rows(LINE_TABLE) if row.get("收件回执") == rid]
    claim_rows = [row for row in store.rows(CLAIM_TABLE) if row.get("收件回执") == rid]

    items = [_public_line(row) for row in line_rows]
    claims = [_public_claim(row) for row in claim_rows]
    counts = {
        "申请数": len(app_rows),
        "地址行数": len(items),
        "可执行地址数": sum(1 for item in items if item["行状态"] in EXECUTABLE_STATUSES),
        "已归属地址数": sum(1 for item in items if item["行状态"] == ITEM_LINKED),
        "已退回地址数": sum(1 for item in items if item["行状态"] == ITEM_REJECTED),
        "待领取数": sum(1 for claim in claims if claim["认领状态"] == "待领取"),
        "已领取数": sum(1 for claim in claims if claim["认领状态"] == "已领取"),
        "已执行数": sum(1 for claim in claims if claim["认领状态"] == "已执行"),
        "新建认领数": len(claims),
        "沿用认领数": sum(1 for item in items if item["行状态"] == ITEM_REUSED),
    }
    counts["已认领数"] = counts["已领取数"] + counts["已执行数"]
    return {
        "回执编号": rid,
        "提交时间": receipt_row.get("提交时间", ""),
        "登记时间": receipt_row.get("登记时间", ""),
        "申请": [_public_application(row) for row in app_rows],
        "逐条明细": items,
        "认领列表": claims,
        "统计": counts,
    }


def get_receipt(receipt_id: str) -> dict[str, Any] | None:
    for row in store.rows(RECEIPT_TABLE):
        if str(row.get("回执编号", "")) == str(receipt_id):
            return _build_receipt(row)
    return None


def list_receipts() -> list[dict[str, Any]]:
    return [
        {
            "回执编号": row.get("回执编号", ""),
            "提交时间": row.get("提交时间", ""),
            "申请数": row.get("申请数", 0),
            "地址行数": row.get("地址行数", 0),
            "可执行地址数": row.get("可执行地址数", 0),
            "归属地址数": row.get("归属地址数", 0),
            "退回地址数": row.get("退回地址数", 0),
            "新建认领数": row.get("新建认领数", 0),
        }
        for row in store.rows(RECEIPT_TABLE)
    ]


def query_application(receipt_id: str, application_id: str) -> dict[str, Any] | None:
    """按回执 + 申请编号追查单份申请：每条的处置、归属和未领走缘由。"""
    receipt = get_receipt(receipt_id)
    if receipt is None:
        return None
    application = next(
        (item for item in receipt["申请"] if item["申请编号"] == str(application_id)), None)
    if application is None:
        return None
    lines = [item for item in receipt["逐条明细"]
             if item["申请编号"] == str(application_id)]
    claims = [item for item in receipt["认领列表"]
              if item["申请编号"] == str(application_id)]
    unclaimed = [
        {
            "行号": item["行号"],
            "原行位置": item["原行位置"],
            "原地址": item["原地址"],
            "状态": item["行状态"],
            "处置结果": item["处置结果"],
            "归属申请": item["归属申请"],
            "归属认领": item["归属认领"],
            "缘由": item["退回缘由"] or "尚未被调度领取",
        }
        for item in lines
        if item["行状态"] not in (ITEM_EXECUTED,)
    ]
    return {
        "回执编号": receipt_id,
        "申请": application,
        "逐条明细": lines,
        "认领列表": claims,
        "未领走明细": unclaimed,
    }


# ---------------------------------------------------------------------------
# 调度认领与执行登记
# ---------------------------------------------------------------------------

def list_claims(receipt_id: str | None = None, status: str | None = None,
                job_id: str | None = None) -> list[dict[str, Any]]:
    rows = list(store.rows(CLAIM_TABLE))
    if receipt_id:
        rows = [row for row in rows if row.get("收件回执") == receipt_id]
    if status:
        rows = [row for row in rows if row.get("认领状态") == status]
    if job_id:
        rows = [row for row in rows if row.get("来源作业") == job_id]
    return [_public_claim(row) for row in rows]


def find_claim(claim_id: str) -> dict[str, Any] | None:
    for row in store.rows(CLAIM_TABLE):
        if str(row.get("认领编号", "")) == str(claim_id):
            return row
    return None


def claim_executable(receipt_id: str | None = None,
                     claim_ids: Iterable[str] | None = None,
                     claimed_by: str = "scheduler",
                     now: datetime | str | None = None) -> dict[str, Any]:
    """调度从入口收件回执认领真正可执行的地址（待领取 → 已领取）。"""
    moment = _now(now)
    wanted = {str(item) for item in (claim_ids or []) if str(item).strip()}
    with _LOCK:
        claimed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for row in store.rows(CLAIM_TABLE):
            if wanted and str(row.get("认领编号")) not in wanted:
                continue
            if receipt_id and str(row.get("收件回执")) != str(receipt_id):
                continue
            state = str(row.get("认领状态", row.get("status", "")))
            if state != "待领取":
                if wanted:
                    skipped.append({
                        "认领编号": row.get("认领编号", ""),
                        "认领状态": state,
                        "缘由": "只有待领取的认领可以被领取",
                    })
                continue
            row.update({
                "认领状态": "已领取",
                "status": "已领取",
                "pending": True,
                "领取人": claimed_by,
                "领取时间": _iso(moment),
            })
            _sync_line_state(row.get("认领编号", ""), ITEM_CLAIMED)
            claimed.append(_public_claim(row))
        return {"已领取": claimed, "跳过": skipped, "数量": len(claimed)}


def _sync_line_state(claim_code: str, status: str) -> None:
    for row in store.rows(LINE_TABLE):
        if str(row.get("认领编号", "")) == str(claim_code):
            row["status"] = status
            row["pending"] = status in (ITEM_PENDING, ITEM_CLAIMED)
            row["abnormal"] = status == ITEM_REJECTED
            break


def record_execution(claim_id: str, application_id: str | None = None,
                     result: str = "成功",
                     now: datetime | str | None = None) -> dict[str, Any]:
    """执行后登记入口由哪份申请提供；提供方必须与认领归属一致。"""
    moment = _now(now)
    with _LOCK:
        claim = find_claim(claim_id)
        if claim is None:
            raise IntakeError(f"认领 {claim_id} 不存在")
        state = str(claim.get("认领状态", ""))
        if state == "已执行":
            raise IntakeError(f"认领 {claim_id} 已登记执行，不能重复登记")
        if state != "已领取":
            raise IntakeError(f"认领 {claim_id} 当前为「{state}」，需先由调度领取")
        provider = str(claim.get("申请编号", ""))
        executed_for = str(application_id or provider)
        if executed_for != provider:
            raise IntakeError(
                f"认领 {claim_id} 的入口由申请 {provider} 提供，不能登记为 {executed_for}")
        claim.update({
            "认领状态": "已执行",
            "status": "已执行",
            "pending": False,
            "执行登记": executed_for,
            "执行时间": _iso(moment),
            "执行结果": result,
        })
        _sync_line_state(claim_id, ITEM_EXECUTED)
        for row in store.rows(LINE_TABLE):
            if str(row.get("认领编号", "")) == str(claim_id):
                row["执行申请"] = executed_for
                row["执行时间"] = _iso(moment)
                break
        return _public_claim(claim)


# ---------------------------------------------------------------------------
# 测试 / 本地重放辅助
# ---------------------------------------------------------------------------

def reset_entry_intake() -> None:
    """清空收件与认领状态（仅供测试和本地重放使用）。"""
    with _LOCK:
        for table in (INTAKE_TABLE, LINE_TABLE, RECEIPT_TABLE, CLAIM_TABLE):
            store.rows(table).clear()
        _SEQ["value"] = 0
