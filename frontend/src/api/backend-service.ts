import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ---------------------------------------------------------------------------
// 入口收件回执 / 调度认领
// ---------------------------------------------------------------------------

export type IntakeAddressLine = {
  url: string
  line_no?: number
  window_start?: string
  window_end?: string
  window_label?: string
}

export type IntakeApplication = {
  application_id?: string
  job_id: string
  source: string
  job_kind?: string
  priority?: number
  window_start?: string
  window_end?: string
  addresses: IntakeAddressLine[]
}

export type IntakeLineResult = {
  申请编号: string
  来源作业: string
  申请类型: string
  分配优先级: number
  行号: number
  原行位置: string
  原地址: string
  地址指纹: string
  活动窗口: string
  行状态: string
  处置结果: string
  归属申请: string
  归属认领: string
  认领编号: string
  执行申请: string
  退回缘由: string
}

export type IntakeApplicationResult = {
  申请编号: string
  来源作业: string
  申请类型: string
  合法来源: string
  分配优先级: number
  活动窗口: string
  提交顺序: number
  状态: string
  地址条数: number
  可执行地址数: number
  归属地址数: number
  退回地址数: number
  退回缘由: string
}

export type IntakeClaim = {
  认领编号: string
  收件回执: string
  申请编号: string
  来源作业: string
  入口地址: string
  地址指纹: string
  原行位置: string
  活动窗口: string
  分配优先级: number
  认领状态: string
  提供申请: string
  执行登记: string
  领取人: string
  领取时间: string
  执行时间: string
  执行结果: string
  关联申请: { 申请编号: string; 来源作业: string; 原行位置: string }[]
}

export type IntakeReceipt = {
  回执编号: string
  提交时间: string
  登记时间: string
  申请: IntakeApplicationResult[]
  逐条明细: IntakeLineResult[]
  认领列表: IntakeClaim[]
  统计: Record<string, number>
}

export async function submitIntake(
  applications: IntakeApplication[],
  now?: string,
): Promise<IntakeReceipt> {
  const response = await request('/api/intake/submit', {
    method: 'POST',
    body: JSON.stringify({ applications, now: now || null }),
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload?.detail ?? '收件提交失败')
  return payload as IntakeReceipt
}

export async function getIntakeReceipt(receiptId: string): Promise<IntakeReceipt> {
  const response = await request(`/api/intake/receipts/${receiptId}`)
  if (!response.ok) throw new Error('收件回执读取失败')
  return (await response.json()) as IntakeReceipt
}

export async function claimIntakeEntries(
  receiptId: string,
  claimIds: string[] = [],
): Promise<{ 已领取: IntakeClaim[]; 跳过: unknown[]; 数量: number }> {
  const response = await request('/api/intake/claims/claim', {
    method: 'POST',
    body: JSON.stringify({ receipt_id: receiptId, claim_ids: claimIds }),
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload?.detail ?? '认领失败')
  return payload
}

export async function recordIntakeExecution(
  claimId: string,
  applicationId?: string,
): Promise<IntakeClaim> {
  const response = await request(`/api/intake/claims/${claimId}/execution`, {
    method: 'POST',
    body: JSON.stringify({ application_id: applicationId || null }),
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload?.detail ?? '执行登记失败')
  return payload as IntakeClaim
}

export async function listIntakeClaims(
  receiptId: string,
  status?: string,
): Promise<IntakeClaim[]> {
  const query = new URLSearchParams({ receipt_id: receiptId })
  if (status) query.set('status', status)
  const response = await request(`/api/intake/claims?${query.toString()}`)
  if (!response.ok) throw new Error('认领列表读取失败')
  return (await response.json()) as IntakeClaim[]
}
