<template>
  <section class="page" data-module="intake">
    <header class="page-head">
      <div>
        <h2>入口收件回执</h2>
        <p class="page-desc">
          一次提交多份采集入口申请，按申请和地址行逐条留痕；同名地址按既有指纹判等，
          同一活动窗口只认领一次，调度可从未领走的行上查到缘由。
        </p>
      </div>
    </header>

    <section class="intake-panel">
      <h3 class="intake-subtitle">提交批次</h3>
      <label class="create-field">
        <span>生效时间（留空取当前时间，可用于回放补采时段）</span>
        <input v-model="nowValue" data-testid="intake-now" placeholder="2026-11-23T10:00:00+00:00" />
      </label>

      <article v-for="(application, index) in applications" :key="index" class="intake-application">
        <header class="intake-application-head">
          <strong>申请 {{ index + 1 }}</strong>
          <button
            v-if="applications.length > 1"
            class="link"
            type="button"
            @click="removeApplication(index)"
          >
            移除
          </button>
        </header>
        <div class="intake-grid">
          <label class="create-field">
            <span>申请编号</span>
            <input v-model="application.application_id" :data-testid="`app-id-${index}`" placeholder="APP-X-DAILY" />
          </label>
          <label class="create-field">
            <span>来源作业</span>
            <input v-model="application.job_id" :data-testid="`app-job-${index}`" placeholder="X" />
          </label>
          <label class="create-field">
            <span>申请类型</span>
            <input v-model="application.job_kind" :data-testid="`app-kind-${index}`" placeholder="日常采集 / 补采" />
          </label>
          <label class="create-field">
            <span>合法来源</span>
            <input v-model="application.source" :data-testid="`app-source-${index}`" placeholder="运营日常采集单" />
          </label>
          <label class="create-field">
            <span>分配优先级（数值大者优先）</span>
            <input v-model.number="application.priority" type="number" :data-testid="`app-priority-${index}`" />
          </label>
          <label class="create-field">
            <span>窗口起</span>
            <input v-model="application.window_start" :data-testid="`app-start-${index}`" placeholder="2026-11-23T00:00:00+00:00" />
          </label>
          <label class="create-field">
            <span>窗口止</span>
            <input v-model="application.window_end" :data-testid="`app-end-${index}`" placeholder="2026-11-24T00:00:00+00:00" />
          </label>
        </div>

        <div class="intake-lines">
          <div class="intake-line-head">
            <span>行</span>
            <span>入口地址</span>
            <span>行窗口起</span>
            <span>行窗口止</span>
            <span></span>
          </div>
          <div v-for="(line, lineIndex) in application.addresses" :key="lineIndex" class="intake-line">
            <span class="intake-line-no">{{ lineIndex + 1 }}</span>
            <input v-model="line.url" :data-testid="`app-${index}-url-${lineIndex}`" placeholder="https://example.com/list?page=1" />
            <input v-model="line.window_start" :data-testid="`app-${index}-line-start-${lineIndex}`" placeholder="留空沿用申请窗口" />
            <input v-model="line.window_end" :data-testid="`app-${index}-line-end-${lineIndex}`" placeholder="留空沿用申请窗口" />
            <button class="link" type="button" @click="removeLine(index, lineIndex)">移除</button>
          </div>
          <button class="btn ghost" type="button" :data-testid="`add-line-${index}`" @click="addLine(index)">
            增加地址行
          </button>
        </div>
      </article>

      <div class="page-actions">
        <button class="btn" type="button" data-testid="add-application" @click="addApplication">
          增加申请
        </button>
        <button class="btn primary" type="button" data-testid="submit-intake" :disabled="submitting" @click="submit">
          {{ submitting ? '提交中…' : '提交收件' }}
        </button>
      </div>
      <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    </section>

    <template v-if="receipt">
      <div class="stat-row">
        <article v-for="card in receiptCards" :key="card.label" class="stat-card">
          <span class="stat-label">{{ card.label }}</span>
          <strong class="stat-value">{{ card.value }}</strong>
        </article>
      </div>

      <section class="intake-panel">
        <h3 class="intake-subtitle">
          回执 {{ receipt.回执编号 }}
          <span class="intake-muted">提交于 {{ receipt.提交时间 }}</span>
        </h3>
        <table class="data-table">
          <thead>
            <tr>
              <th>申请编号</th><th>来源作业</th><th>申请类型</th><th>分配优先级</th>
              <th>活动窗口</th><th>状态</th><th>可执行</th><th>归属他人</th><th>退回</th><th>缘由</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="application in receipt.申请" :key="application.申请编号" :data-testid="`receipt-app-${application.申请编号}`">
              <td>{{ application.申请编号 }}</td>
              <td>{{ application.来源作业 }}</td>
              <td>{{ application.申请类型 }}</td>
              <td>{{ application.分配优先级 }}</td>
              <td>{{ application.活动窗口 }}</td>
              <td>{{ application.状态 }}</td>
              <td>{{ application.可执行地址数 }}</td>
              <td>{{ application.归属地址数 }}</td>
              <td>{{ application.退回地址数 }}</td>
              <td class="intake-reason">{{ application.退回缘由 || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="intake-panel">
        <h3 class="intake-subtitle">逐条明细</h3>
        <table class="data-table">
          <thead>
            <tr>
              <th>申请</th><th>行</th><th>原地址</th><th>活动窗口</th><th>行状态</th>
              <th>处置结果</th><th>归属申请</th><th>归属认领</th><th>退回缘由</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="line in receipt.逐条明细" :key="line.原行位置 + line.原地址">
              <td>{{ line.申请编号 }}</td>
              <td>{{ line.行号 }}</td>
              <td class="intake-url">{{ line.原地址 || '—' }}</td>
              <td>{{ line.活动窗口 }}</td>
              <td :data-testid="`line-status-${line.原行位置}`">{{ line.行状态 }}</td>
              <td>{{ line.处置结果 }}</td>
              <td>{{ line.归属申请 || '—' }}</td>
              <td>{{ line.归属认领 || '—' }}</td>
              <td class="intake-reason">{{ line.退回缘由 || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <p class="intake-muted">
          查询某份申请未领走的原因：
          <template v-for="application in receipt.申请" :key="application.申请编号">
            <button class="link" type="button" @click="inspect(application.申请编号)">
              {{ application.申请编号 }}
            </button>
            <span> </span>
          </template>
        </p>
      </section>

      <section class="intake-panel">
        <h3 class="intake-subtitle">调度认领列表</h3>
        <div class="page-actions intake-actions">
          <button class="btn" type="button" data-testid="claim-all" :disabled="!hasPending" @click="claimAll">
            认领回执内全部可执行地址
          </button>
        </div>
        <table class="data-table">
          <thead>
            <tr>
              <th>认领编号</th><th>来源作业</th><th>入口地址</th><th>原行位置</th>
              <th>认领状态</th><th>提供申请</th><th>执行登记</th><th>关联申请</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="claim in receipt.认领列表" :key="claim.认领编号" :data-testid="`claim-${claim.认领编号}`">
              <td>{{ claim.认领编号 }}</td>
              <td>{{ claim.来源作业 }}</td>
              <td class="intake-url">{{ claim.入口地址 }}</td>
              <td>{{ claim.原行位置 }}</td>
              <td>{{ claim.认领状态 }}</td>
              <td>{{ claim.提供申请 }}</td>
              <td>{{ claim.执行登记 || '—' }}</td>
              <td class="intake-reason">
                <span v-for="item in claim.关联申请" :key="item.申请编号 + item.原行位置">
                  {{ item.申请编号 }}（{{ item.原行位置 }}）
                </span>
              </td>
              <td>
                <button
                  v-if="claim.认领状态 === '待领取'"
                  class="link"
                  type="button"
                  @click="claimOne(claim.认领编号)"
                >
                  领取
                </button>
                <button
                  v-else-if="claim.认领状态 === '已领取'"
                  class="link"
                  type="button"
                  @click="execute(claim.认领编号)"
                >
                  登记执行
                </button>
                <span v-else>—</span>
              </td>
            </tr>
            <tr v-if="!receipt.认领列表.length">
              <td colspan="9" class="empty-state">本回执没有可执行地址，全部退回或归属他人</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="inspection" class="intake-panel">
        <h3 class="intake-subtitle">申请 {{ inspection.申请.申请编号 }} 的未领走明细</h3>
        <table class="data-table">
          <thead>
            <tr><th>行</th><th>原地址</th><th>状态</th><th>处置结果</th><th>归属申请</th><th>缘由</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in inspection.未领走明细" :key="item.原行位置">
              <td>{{ item.行号 }}</td>
              <td class="intake-url">{{ item.原地址 || '—' }}</td>
              <td>{{ item.状态 }}</td>
              <td>{{ item.处置结果 }}</td>
              <td>{{ item.归属申请 || '—' }}</td>
              <td class="intake-reason">{{ item.缘由 }}</td>
            </tr>
            <tr v-if="!inspection.未领走明细.length">
              <td colspan="6" class="empty-state">这份申请的全部地址都已执行</td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import {
  claimIntakeEntries,
  getIntakeReceipt,
  recordIntakeExecution,
  submitIntake,
} from '@/api/backend-service'
import type {
  IntakeApplication,
  IntakeApplicationResult,
  IntakeLineResult,
  IntakeReceipt,
} from '@/api/backend-service'

type DraftLine = { url: string; window_start: string; window_end: string }
type DraftApplication = Omit<IntakeApplication, 'addresses'> & { addresses: DraftLine[] }

type Inspection = {
  申请: IntakeApplicationResult
  逐条明细: IntakeLineResult[]
  未领走明细: {
    行号: number
    原行位置: string
    原地址: string
    状态: string
    处置结果: string
    归属申请: string
    缘由: string
  }[]
}

const emptyLine = (): DraftLine => ({ url: '', window_start: '', window_end: '' })
const emptyApplication = (index: number): DraftApplication => ({
  application_id: '',
  job_id: '',
  source: '',
  job_kind: index === 0 ? '日常采集' : '补采',
  priority: index === 0 ? 10 : 30,
  window_start: '',
  window_end: '',
  addresses: [emptyLine()],
})

const applications = ref<DraftApplication[]>([emptyApplication(0)])
const nowValue = ref('')
const receipt = ref<IntakeReceipt | null>(null)
const inspection = ref<Inspection | null>(null)
const errorMessage = ref('')
const submitting = ref(false)

const receiptCards = computed(() => {
  const stats = receipt.value?.统计 ?? {}
  return [
    { label: '可执行地址', value: stats['可执行地址数'] ?? 0 },
    { label: '待领取', value: stats['待领取数'] ?? 0 },
    { label: '已领取', value: stats['已领取数'] ?? 0 },
    { label: '已执行', value: stats['已执行数'] ?? 0 },
    { label: '归属他人', value: stats['已归属地址数'] ?? 0 },
    { label: '已退回', value: stats['已退回地址数'] ?? 0 },
  ]
})

const hasPending = computed(
  () => (receipt.value?.认领列表 ?? []).some((claim) => claim.认领状态 === '待领取'),
)

function addApplication() {
  applications.value.push(emptyApplication(applications.value.length))
}

function removeApplication(index: number) {
  applications.value.splice(index, 1)
}

function addLine(index: number) {
  applications.value[index].addresses.push(emptyLine())
}

function removeLine(applicationIndex: number, lineIndex: number) {
  const lines = applications.value[applicationIndex].addresses
  if (lines.length <= 1) return
  lines.splice(lineIndex, 1)
}

async function submit() {
  submitting.value = true
  errorMessage.value = ''
  inspection.value = null
  try {
    const payload: IntakeApplication[] = applications.value.map((application) => ({
      application_id: application.application_id || undefined,
      job_id: application.job_id,
      source: application.source,
      job_kind: application.job_kind,
      priority: Number(application.priority),
      window_start: application.window_start || undefined,
      window_end: application.window_end || undefined,
      addresses: application.addresses
        .filter((line) => line.url.trim())
        .map((line, index) => ({
          url: line.url.trim(),
          line_no: index + 1,
          window_start: line.window_start || undefined,
          window_end: line.window_end || undefined,
        })),
    }))
    receipt.value = await submitIntake(payload, nowValue.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '收件失败'
  } finally {
    submitting.value = false
  }
}

async function refresh() {
  if (!receipt.value) return
  receipt.value = await getIntakeReceipt(receipt.value.回执编号)
  if (inspection.value) {
    await inspect(inspection.value.申请.申请编号)
  }
}

async function inspect(applicationId: string) {
  if (!receipt.value) return
  const response = await fetch(
    `/api/intake/receipts/${receipt.value.回执编号}/applications/${applicationId}`,
  )
  if (!response.ok) return
  inspection.value = (await response.json()) as Inspection
}

async function claimAll() {
  if (!receipt.value) return
  await claimIntakeEntries(receipt.value.回执编号)
  await refresh()
}

async function claimOne(claimId: string) {
  if (!receipt.value) return
  await claimIntakeEntries(receipt.value.回执编号, [claimId])
  await refresh()
}

async function execute(claimId: string) {
  await recordIntakeExecution(claimId)
  await refresh()
}
</script>
