"""入口收件回执测试：批量收件、同窗口归属、坏地址隔离、重送不改归属、执行登记。"""
from __future__ import annotations

import pytest

from app import entry_intake as intake
from app.integration import (
    receive_entry_applications, register_entry_execution, scheduler_claim_entries,
)
from app.entry_intake import list_claims
from app.store import store


NOW = "2026-11-23T10:00:00+00:00"
DAY_START = "2026-11-23T00:00:00+00:00"
DAY_END = "2026-11-24T00:00:00+00:00"
MISSED_START = "2026-11-20T00:00:00+00:00"
MISSED_END = "2026-11-21T00:00:00+00:00"

SHARED_URL_A = "https://x.example.com/list?page=1&sort=new"
SHARED_URL_B = "https://x.example.com/list?sort=new&page=1"
BAD_URL = "https://x.example.com/broken path"
Y_URL = "https://y.example.com/feed"


@pytest.fixture(autouse=True)
def clean_intake() -> None:
    intake.reset_entry_intake()
    yield
    intake.reset_entry_intake()


def _apply(receipt: dict, application_id: str) -> dict:
    detail = intake.query_application(receipt["回执编号"], application_id)
    assert detail is not None
    return detail


def _daily(addresses=None, priority: int = 10) -> dict:
    return {
        "application_id": "APP-X-DAILY",
        "job_id": "X",
        "source": "运营日常采集单",
        "job_kind": "日常采集",
        "priority": priority,
        "window_start": DAY_START,
        "window_end": DAY_END,
        "addresses": addresses if addresses is not None else [
            {"url": SHARED_URL_A, "line_no": 1},
            {"url": BAD_URL, "line_no": 2},
        ],
    }


def _makeup(addresses=None, priority: int = 30) -> dict:
    return {
        "application_id": "APP-X-MAKEUP",
        "job_id": "X",
        "source": "运营补采单",
        "job_kind": "补采",
        "priority": priority,
        "window_start": DAY_START,
        "window_end": DAY_END,
        "addresses": addresses if addresses is not None else [
            {"url": SHARED_URL_B, "line_no": 1,
             "window_start": MISSED_START, "window_end": MISSED_END},
        ],
    }


def _job_y() -> dict:
    return {
        "application_id": "APP-Y",
        "job_id": "Y",
        "source": "Y站日常单",
        "job_kind": "日常采集",
        "priority": 10,
        "window_start": DAY_START,
        "window_end": DAY_END,
        "addresses": [{"url": Y_URL, "line_no": 1}],
    }


# ---------------------------------------------------------------------------
# 逐条回执与同窗口归属
# ---------------------------------------------------------------------------

def test_daily_receipt_keeps_original_lines_and_rejects_bad_address() -> None:
    receipt = receive_entry_applications([_daily()], now=NOW)

    assert receipt["统计"]["地址行数"] == 2
    detail = _apply(receipt, "APP-X-DAILY")
    first, second = detail["逐条明细"]
    assert first["原地址"] == SHARED_URL_A, "回执应保留原地址"
    assert first["行号"] == 1, "回执应保留原行位置"
    assert first["行状态"] == intake.ITEM_PENDING
    assert first["活动窗口"], "回执应保留活动窗口"
    assert second["原地址"] == BAD_URL
    assert second["行状态"] == intake.ITEM_REJECTED
    assert "地址不合法" in second["退回缘由"]
    assert detail["申请"]["状态"] == "部分退回"
    assert detail["申请"]["可执行地址数"] == 1


def test_makeup_priority_yields_shared_address_to_better_application() -> None:
    daily = receive_entry_applications([_daily()], now=NOW)
    daily_claim = next(c for c in list_claims(daily["回执编号"]))
    assert daily_claim["入口地址"] == SHARED_URL_A
    # 补采单把同一地址的窗口收紧到一个已经错过的时段，同时给更高优先级
    makeup = receive_entry_applications([_makeup()], now=NOW)

    detail = _apply(makeup, "APP-X-MAKEUP")
    line = detail["逐条明细"][0]
    assert line["行状态"] == intake.ITEM_REJECTED
    assert line["处置结果"] == "时段已错过退回"
    assert detail["申请"]["状态"] == "全部退回"
    assert not list_claims(makeup["回执编号"]), "错过的时段不应产生新认领"


def test_makeup_wins_shared_address_when_arriving_first_in_same_window() -> None:
    """同一有效窗口内并发确认时，更高优先级申请取得唯一凭证。"""
    makeup = dict(_makeup())
    makeup["addresses"] = [{"url": SHARED_URL_B, "line_no": 1}]
    receipt = receive_entry_applications([_daily(), makeup], now=NOW)

    daily_line = _apply(receipt, "APP-X-DAILY")["逐条明细"][0]
    makeup_line = _apply(receipt, "APP-X-MAKEUP")["逐条明细"][0]

    assert makeup_line["行状态"] == intake.ITEM_PENDING
    assert makeup_line["归属申请"] == "APP-X-MAKEUP"
    assert daily_line["行状态"] == intake.ITEM_LINKED
    assert daily_line["归属申请"] == "APP-X-MAKEUP"
    assert daily_line["归属认领"] == makeup_line["归属认领"], \
        "另一份申请必须关联到实际归属的认领"
    # 同一地址只产生一次认领
    shared_claims = [
        c for c in list_claims() if c["地址指纹"] == daily_line["地址指纹"]]
    assert len(shared_claims) == 1
    assert shared_claims[0]["申请编号"] == "APP-X-MAKEUP"
    assert {"申请编号": "APP-X-DAILY", "来源作业": "X",
            "原行位置": "APP-X-DAILY#1"} in shared_claims[0]["关联申请"]


def test_address_equivalence_uses_existing_fingerprint() -> None:
    assert intake.address_fingerprint(SHARED_URL_A) == intake.address_fingerprint(SHARED_URL_B)
    assert intake.address_fingerprint(SHARED_URL_A) != intake.address_fingerprint(Y_URL)


def test_bad_address_does_not_block_other_jobs() -> None:
    receipt = receive_entry_applications([_daily(), _job_y()], now=NOW)

    assert _apply(receipt, "APP-Y")["申请"]["状态"] == "已确认"
    y_claims = [
        c for c in list_claims(receipt["回执编号"]) if c["来源作业"] == "Y"]
    assert [c["入口地址"] for c in y_claims] == [Y_URL]


# ---------------------------------------------------------------------------
# 调度认领与执行登记
# ---------------------------------------------------------------------------

def test_scheduler_claims_only_executable_addresses_and_records_provider() -> None:
    receipt = receive_entry_applications([_daily(), _job_y()], now=NOW)
    claimable = list_claims(receipt["回执编号"], status="待领取")
    assert len(claimable) == 2
    assert all(c["认领状态"] == "待领取" for c in claimable)

    claimed = scheduler_claim_entries(receipt["回执编号"])
    assert claimed["数量"] == 2
    assert not list_claims(receipt["回执编号"], status="待领取")
    assert len(list_claims(receipt["回执编号"], status="已领取")) == 2

    x_claim = next(c for c in claimed["已领取"] if c["来源作业"] == "X")
    executed = register_entry_execution(x_claim["认领编号"])
    assert executed["执行登记"] == "APP-X-DAILY"
    assert executed["认领状态"] == "已执行"
    assert executed["提供申请"] == "APP-X-DAILY"

    line = _apply(receipt, "APP-X-DAILY")["逐条明细"][0]
    assert line["行状态"] == intake.ITEM_EXECUTED
    assert line["执行申请"] == "APP-X-DAILY"

    bad_line = _apply(receipt, "APP-X-DAILY")["逐条明细"][1]
    assert bad_line["行状态"] == intake.ITEM_REJECTED
    assert "地址不合法" in bad_line["退回缘由"], "未领走的申请可照回执照到缘由"


def test_execution_cannot_be_registered_for_another_application() -> None:
    receipt = receive_entry_applications([_daily()], now=NOW)
    claim = scheduler_claim_entries(receipt["回执编号"])["已领取"][0]

    with pytest.raises(Exception) as excinfo:
        register_entry_execution(claim["认领编号"], application_id="APP-X-MAKEUP")
    assert "提供" in str(excinfo.value)


def test_execution_requires_claim_first() -> None:
    receipt = receive_entry_applications([_daily()], now=NOW)
    claim = list_claims(receipt["回执编号"], status="待领取")[0]

    with pytest.raises(Exception) as excinfo:
        register_entry_execution(claim["认领编号"])
    assert "领取" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 重送不改归属，坏地址改正后旧网址不再次领走
# ---------------------------------------------------------------------------

def test_resubmission_cannot_steal_existing_claim() -> None:
    receipt = receive_entry_applications([_daily()], now=NOW)
    original_claim = list_claims(receipt["回执编号"])[0]

    # 同一地址重送并抬高优先级，仍然沿用原认领
    replay = receive_entry_applications([_daily(priority=99)], now=NOW)

    replay_line = _apply(replay, "APP-X-DAILY")["逐条明细"][0]
    assert replay_line["行状态"] == intake.ITEM_REUSED
    assert replay_line["归属认领"] == original_claim["认领编号"]
    assert replay["统计"]["新建认领数"] == 0, "重送不应新建认领"
    current = [c for c in list_claims() if c["地址指纹"] == original_claim["地址指纹"]]
    assert len(current) == 1
    assert current[0]["申请编号"] == original_claim["申请编号"]


def test_fixed_bad_address_proceeds_while_old_claim_stays() -> None:
    receipt = receive_entry_applications([_daily(), _job_y()], now=NOW)
    scheduler_claim_entries(receipt["回执编号"])
    old_claim = next(c for c in list_claims(receipt["回执编号"]) if c["来源作业"] == "X")
    register_entry_execution(old_claim["认领编号"])

    fixed = _daily(addresses=[
        {"url": SHARED_URL_A, "line_no": 1},
        {"url": "https://x.example.com/fixed/page", "line_no": 2},
    ])
    fixed["addresses"][1]["window_start"] = DAY_START
    fixed["addresses"][1]["window_end"] = DAY_END
    resubmit = receive_entry_applications([fixed], now=NOW)

    lines = _apply(resubmit, "APP-X-DAILY")["逐条明细"]
    assert lines[0]["行状态"] == intake.ITEM_REUSED
    assert lines[0]["归属认领"] == old_claim["认领编号"]
    assert lines[1]["行状态"] == intake.ITEM_PENDING
    assert lines[1]["原地址"] == "https://x.example.com/fixed/page"
    assert resubmit["统计"]["新建认领数"] == 1, "只应为改正后的新地址新建认领"

    y_claim = next(c for c in list_claims() if c["来源作业"] == "Y")
    assert y_claim["入口地址"] == Y_URL, "Y 的合法地址仍可执行"


def test_rejected_bad_address_url_is_not_claimed_after_fix() -> None:
    """改正坏地址时，坏地址的旧网址没有留下认领。"""
    receive_entry_applications([_daily()], now=NOW)
    assert not [c for c in list_claims() if c["入口地址"] == BAD_URL]

    fixed = _daily(addresses=[
        {"url": SHARED_URL_A, "line_no": 1},
        {"url": "https://x.example.com/good/page", "line_no": 2},
    ])
    receipt = receive_entry_applications([fixed], now=NOW)
    urls = [c["入口地址"] for c in list_claims(receipt["回执编号"])]
    assert urls == ["https://x.example.com/good/page"]
    assert BAD_URL not in urls


# ---------------------------------------------------------------------------
# 状态与查询
# ---------------------------------------------------------------------------

def test_receipt_is_queryable_and_lists_unclaimed_reasons() -> None:
    receipt = receive_entry_applications([_daily(), _job_y()], now=NOW)
    listed = intake.list_receipts()
    assert any(item["回执编号"] == receipt["回执编号"] for item in listed)

    detail = _apply(receipt, "APP-X-DAILY")
    reasons = {item["行号"]: item["缘由"] for item in detail["未领走明细"]}
    assert "地址不合法" in reasons[2]

    assert intake.query_application(receipt["回执编号"], "APP-NOT-EXIST") is None


def test_fingerprints_are_registered_in_url_dedup() -> None:
    receipt = receive_entry_applications([_daily(), _job_y()], now=NOW)

    hashes = {row["指纹哈希"] for row in store.rows("url_dedup")}
    assert intake.address_fingerprint(SHARED_URL_A) in hashes
    assert intake.address_fingerprint(Y_URL) in hashes
    assert receipt["统计"]["申请数"] == 2
    assert _apply(receipt, "APP-X-DAILY")["逐条明细"][1]["地址指纹"] == "", \
        "坏地址不登记指纹"


def test_duplicate_application_ids_in_one_batch_are_rejected() -> None:
    with pytest.raises(intake.IntakeError):
        receive_entry_applications([_daily(), _daily()], now=NOW)


def test_missing_source_is_rejected_per_application() -> None:
    broken = dict(_job_y())
    broken["source"] = ""
    receipt = receive_entry_applications([broken, _daily()], now=NOW)

    assert _apply(receipt, "APP-Y")["申请"]["状态"] == "全部退回"
    assert _apply(receipt, "APP-X-DAILY")["申请"]["可执行地址数"] == 1


def test_empty_batch_is_rejected() -> None:
    with pytest.raises(intake.IntakeError):
        receive_entry_applications([], now=NOW)


def test_later_application_with_higher_priority_cannot_steal_claimed_address() -> None:
    """已被领走的地址不因后来申请优先级更高而改归属。"""
    daily = receive_entry_applications([_daily()], now=NOW)
    scheduler_claim_entries(daily["回执编号"])
    original = next(c for c in list_claims(daily["回执编号"]) if c["来源作业"] == "X")

    makeup = dict(_makeup())
    makeup["addresses"] = [{"url": SHARED_URL_B, "line_no": 1}]
    later = receive_entry_applications([makeup], now=NOW)

    line = _apply(later, "APP-X-MAKEUP")["逐条明细"][0]
    assert line["行状态"] == intake.ITEM_LINKED
    assert line["归属申请"] == "APP-X-DAILY"
    assert line["归属认领"] == original["认领编号"]
    shared = [c for c in list_claims() if c["地址指纹"] == original["地址指纹"]]
    assert len(shared) == 1
    assert shared[0]["认领编号"] == original["认领编号"]
    assert {item["申请编号"] for item in shared[0]["关联申请"]} == {"APP-X-MAKEUP"}


def test_concurrent_confirmations_claim_shared_address_once() -> None:
    """两个线程同时确认同一地址，只允许一份取得凭证。"""
    import threading

    barrier = threading.Barrier(2)
    results: list[dict] = []
    errors: list[BaseException] = []

    def submit(app: dict) -> None:
        try:
            barrier.wait(timeout=5)
            results.append(receive_entry_applications([app], now=NOW))
        except BaseException as exc:  # noqa: BLE001 - 测试需要看到线程内异常
            errors.append(exc)

    daily = dict(_daily(addresses=[{"url": SHARED_URL_A, "line_no": 1}]))
    makeup = dict(_makeup(addresses=[{"url": SHARED_URL_B, "line_no": 1}]))
    threads = [threading.Thread(target=submit, args=(daily,)),
               threading.Thread(target=submit, args=(makeup,))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, f"并发收件不应报错：{errors}"
    statuses = []
    for receipt in results:
        application_id = receipt["申请"][0]["申请编号"]
        statuses.append(_apply(receipt, application_id)["逐条明细"][0]["行状态"])
    statuses.sort()
    assert statuses.count(intake.ITEM_PENDING) == 1, statuses
    assert statuses.count(intake.ITEM_LINKED) == 1, statuses
    shared = [c for c in list_claims()
              if c["地址指纹"] == intake.address_fingerprint(SHARED_URL_A)]
    assert len(shared) == 1, "并发确认也只能生成一次认领"
