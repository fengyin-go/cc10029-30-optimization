<template>
  <section class="page" data-module="selector">
    <header class="page-head">
      <div>
        <h2>抽取规则管理</h2>
        <p class="page-desc">管理各站点的 CSS/XPath 选择器和分页策略，读取样本生成选择器校验数据报告，按规则字段定位空分页、匹配失败与字段缺失。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记选择器规则</button>
        <button class="btn" data-testid="open-samples" type="button" @click="openSamples">校验样本</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出抽取规则清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>校验结论</th>
          <th>版本</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <template v-if="row.validation_summary">
              <span :class="['report-badge', badgeClass(row.validation_summary.status)]">{{ row.validation_summary.status }}</span>
              <span v-if="row.validation_summary.blocking_fields.length" class="block-hint">
                阻断：{{ row.validation_summary.blocking_fields.join('、') }}
              </span>
              <span v-else class="ok-hint">无阻断项</span>
            </template>
            <span v-else class="muted-hint">未校验</span>
          </td>
          <td>
            草稿 v{{ row.draft_version ?? 1 }}<br />
            <span class="muted-hint">发布 {{ row.published_version ? 'v' + row.published_version : '无' }}</span>
          </td>
          <td class="row-actions report-actions">
            <button class="link" data-testid="btn-validate" type="button" @click="validateRule(row)">校验规则</button>
            <button class="link" data-testid="btn-report" type="button" @click="openReport(row)">校验报告</button>
            <button class="link" data-testid="btn-edit" type="button" @click="openEdit(row)">修正规则</button>
            <button class="link" data-testid="btn-publish" type="button" @click="publishRule(row)">发布版本</button>
            <button class="link" data-testid="btn-conflict" type="button" @click="openConflict(row)">版本冲突</button>
            <button class="link" data-testid="btn-drive" type="button" @click="driveExtraction(row)">驱动数据提取</button>
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 3" class="empty-state">暂无抽取规则数据，可先登记选择器规则</td>
        </tr>
      </tbody>
    </table>

    <!-- 登记规则 -->
    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记选择器规则</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <!-- 修正规则：形成新草稿版本 -->
    <div v-if="showEdit" class="modal-backdrop" data-testid="edit-modal">
      <form class="modal" @submit.prevent="submitEdit">
        <h3>修正选择器规则（{{ editRow?.规则编号 }}）</h3>
        <p class="page-desc">任一字段变化都会生成新的草稿版本，发布版本保持不动；修正后需重新校验并复核阻断项。</p>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="editValues[field]" :data-testid="`edit-${field}`" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showEdit = false">取消</button>
          <button class="btn primary" data-testid="submit-edit" type="submit" :disabled="saving">
            {{ saving ? '提交中…' : '保存并生成新版本' }}
          </button>
        </div>
      </form>
    </div>

    <!-- 选择器校验数据报告 -->
    <div v-if="showReport" class="modal-backdrop" data-testid="report-modal">
      <div class="modal modal-wide">
        <h3>选择器校验数据报告 {{ currentReport?.report_no }}</h3>
        <section class="report-meta">
          <dl><dt>规则编号</dt><dd>{{ currentReport?.rule_code }}</dd></dl>
          <dl><dt>规则版本</dt><dd>v{{ currentReport?.rule_version }}（{{ currentReport?.rule_version_phase === 'published' ? '已发布' : '草稿' }}）</dd></dl>
          <dl><dt>绑定样本</dt><dd>{{ currentReport?.sample_id }} {{ currentReport?.sample_name }}</dd></dl>
          <dl><dt>采样版本</dt><dd>{{ currentReport?.sample_version }}</dd></dl>
          <dl><dt>报告状态</dt><dd><span :class="['report-badge', badgeClass(currentReport?.status)]">{{ currentReport?.status }}</span></dd></dl>
          <dl><dt>完整性</dt><dd>{{ currentReport?.integrity_ok === false ? '存在缺陷' : '核对通过' }}</dd></dl>
        </section>

        <table class="data-table report-table">
          <thead>
            <tr><th>规则字段</th><th>选择器表达式</th><th>错误分类</th><th>阻断结论</th><th>命中数</th><th>失败原因/说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in currentReport?.items ?? []" :key="item.field" :data-testid="`report-item-${item.field}`">
              <td>{{ item.field }}</td>
              <td class="expr-cell">{{ item.expression || '—' }}</td>
              <td><span :class="['cat-badge', categoryClass(item.category)]">{{ item.category_label }}</span></td>
              <td><span :class="['state-badge', stateClass(item.state)]">{{ item.state }}</span></td>
              <td>{{ item.matched }}</td>
              <td>{{ item.detail }}<span v-if="item.resolved_at" class="resolved-at">（{{ item.resolved_at }} 解除）</span></td>
            </tr>
          </tbody>
        </table>

        <div v-if="currentReport?.integrity_errors?.length" class="defect-box" data-testid="defect-box">
          <strong>完整性缺陷：</strong>
          <ul><li v-for="(err, idx) in currentReport.integrity_errors" :key="idx">{{ err }}</li></ul>
        </div>

        <h4>历史报告版本（{{ reportHistory.length }} 份，规则只绑定其中一个编号）</h4>
        <ul class="history-list">
          <li v-for="rep in reportHistory" :key="rep.report_no" :data-testid="`history-${rep.report_no}`">
            <button class="link" type="button" @click="loadReport(rep.report_no)">{{ rep.report_no }}</button>
            规则 v{{ rep.rule_version }} · 样本 {{ rep.sample_id }}（{{ rep.sample_version }}）· {{ rep.status }}
            <span v-if="rep.report_no === currentReport?.report_no" class="ok-hint">← 当前绑定</span>
          </li>
        </ul>

        <div class="page-actions">
          <button class="btn primary" data-testid="revalidate" type="button" :disabled="busy" @click="revalidate">再次校验（同版本复核）</button>
          <button class="btn" data-testid="integrity-self" type="button" :disabled="busy" @click="checkIntegrity(false)">完整性核对</button>
          <button class="btn" data-testid="integrity-tamper" type="button" :disabled="busy" @click="checkIntegrity(true)">模拟残缺送交核对</button>
          <button class="btn ghost" type="button" @click="showReport = false">关闭</button>
        </div>
      </div>
    </div>

    <!-- 版本冲突 -->
    <div v-if="showConflict" class="modal-backdrop" data-testid="conflict-modal">
      <div class="modal">
        <h3>草稿/发布版本冲突核对</h3>
        <dl class="kv-list">
          <dt>草稿版本</dt><dd>v{{ conflictInfo?.draft_version }}</dd>
          <dt>发布版本</dt><dd>{{ conflictInfo?.published_version ? 'v' + conflictInfo.published_version : '无发布版本' }}</dd>
          <dt>是否冲突</dt><dd>{{ conflictInfo?.conflict ? '存在版本冲突' : '无冲突' }}</dd>
          <dt>唯一绑定报告</dt><dd>{{ conflictInfo?.bound_report_no ?? '无' }}</dd>
          <dt>报告绑定规则版本</dt><dd>{{ conflictInfo?.bound_rule_version ?? '—' }}</dd>
          <dt>报告绑定采样版本</dt><dd>{{ conflictInfo?.bound_sample_id ?? '—' }}</dd>
        </dl>
        <p class="page-desc">{{ conflictInfo?.message }}</p>
        <div class="page-actions"><button class="btn ghost" type="button" @click="showConflict = false">关闭</button></div>
      </div>
    </div>

    <!-- 执行记录回执 -->
    <div v-if="showRecord" class="modal-backdrop" data-testid="record-modal">
      <div class="modal">
        <h3>选择器执行记录回执</h3>
        <dl class="kv-list">
          <dt>执行编号</dt><dd>{{ executionRecord?.record_no }}</dd>
          <dt>来源报告</dt><dd>{{ executionRecord?.report_no }}</dd>
          <dt>规则版本</dt><dd>v{{ executionRecord?.rule_version }}</dd>
          <dt>采样版本</dt><dd>{{ executionRecord?.sample_id }}（{{ executionRecord?.sample_version }}）</dd>
          <dt>受理状态</dt><dd><span :class="['state-badge', executionRecord?.status === '已受理' ? 'state-resolved' : 'state-blocked']">{{ executionRecord?.status }}</span></dd>
          <dt>原因</dt><dd>{{ executionRecord?.reason }}</dd>
        </dl>
        <div class="page-actions"><button class="btn ghost" type="button" @click="showRecord = false">关闭</button></div>
      </div>
    </div>

    <!-- 校验样本 -->
    <div v-if="showSamples" class="modal-backdrop" data-testid="samples-modal">
      <div class="modal modal-wide">
        <h3>选择器校验样本（按归属站点绑定实际采样版本）</h3>
        <table class="data-table report-table">
          <thead><tr><th>样本编号</th><th>样本名称</th><th>归属站点</th><th>采样版本</th><th>分页控件</th></tr></thead>
          <tbody>
            <tr v-for="s in samples" :key="s.sample_id">
              <td>{{ s.sample_id }}</td><td>{{ s.sample_name }}</td><td>{{ s.site_name }}</td>
              <td>{{ s.sample_version }}</td><td>{{ s.pagination?.selector || '无（单页结构）' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="page-actions"><button class="btn ghost" type="button" @click="showSamples = false">关闭</button></div>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条抽取规则记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="infoMessage" class="ok-text">{{ infoMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>
interface ReportItem {
  field: string
  expression: string
  category: 'PASS' | 'EMPTY_PAGINATION' | 'MATCH_FAIL' | 'FIELD_MISSING'
  category_label: string
  blocking: boolean
  matched: number
  detail: string
  state: '通过' | '提示' | '阻断' | '已解除'
  resolved_at: string | null
}
interface ValidationReport {
  report_no: string
  entry_id: number
  rule_code: string
  sample_id: string
  sample_name: string
  sample_version: string
  rule_version: number
  rule_version_phase: 'draft' | 'published'
  status: string
  blocking: boolean
  integrity_ok: boolean
  integrity_errors: string[]
  items: ReportItem[]
}

const ENDPOINT = '/api/selector'
const columns = ['规则编号', '归属站点', '列表选择器', '详情选择器', '字段选择器', '分页参数', '等待条件', '字符编码']
const actions = ['提交校验', '启用配置', '停用配置']
const statuses = ['草稿', '待校验', '已启用', '已停用']
const metricNames = ['选择器规则总数', '待校验选择器规则', '已启用选择器规则']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const infoMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})

const showEdit = ref(false)
const saving = ref(false)
const editRow = ref<Row | null>(null)
const editValues = ref<Record<string, string>>({})

const showReport = ref(false)
const busy = ref(false)
const currentReport = ref<ValidationReport | null>(null)
const reportHistory = ref<ValidationReport[]>([])
const activeRow = ref<Row | null>(null)

const showConflict = ref(false)
const conflictInfo = ref<Record<string, any> | null>(null)
const showRecord = ref(false)
const executionRecord = ref<Record<string, any> | null>(null)
const showSamples = ref(false)
const samples = ref<Row[]>([])

const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

function badgeClass(status?: string) {
  if (status === '已通过') return 'state-resolved'
  if (status === '阻断中') return 'state-blocked'
  if (status === '缺陷') return 'state-defect'
  return 'state-pass'
}
function categoryClass(category: string) {
  if (category === 'PASS') return 'cat-pass'
  if (category === 'EMPTY_PAGINATION') return 'cat-empty'
  if (category === 'MATCH_FAIL') return 'cat-match'
  return 'cat-missing'
}
function stateClass(state: string) {
  if (state === '阻断') return 'state-blocked'
  if (state === '已解除') return 'state-resolved'
  if (state === '提示') return 'state-hint'
  return 'state-pass'
}

function flash(message: string, ok = true) {
  if (ok) {
    infoMessage.value = message
    errorMessage.value = ''
  } else {
    errorMessage.value = message
    infoMessage.value = ''
  }
}

async function readJson(response: Response) {
  const payload = await response.json()
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.detail || payload.message || '请求失败')
  }
  return payload
}

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'selector.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    flash(error instanceof Error ? error.message : '导出失败', false)
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, { method: 'POST', body: JSON.stringify(createValues.value) })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
    flash('选择器规则已登记')
  } catch (error) {
    flash(error instanceof Error ? error.message : '登记失败', false)
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) throw new Error('抽取规则动作未生效，请稍后重试')
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '抽取规则操作失败', false)
  }
}

function openEdit(row: Row) {
  editRow.value = row
  editValues.value = Object.fromEntries(columns.map((field) => [field, String(row[field] ?? '')]))
  showEdit.value = true
}

async function submitEdit() {
  if (!editRow.value) return
  saving.value = true
  try {
    const response = await request(`${ENDPOINT}/${editRow.value.id}`, {
      method: 'PUT',
      body: JSON.stringify({ values: editValues.value }),
    })
    const payload = await readJson(response)
    showEdit.value = false
    await reload()
    flash(payload.message)
  } catch (error) {
    flash(error instanceof Error ? error.message : '修正失败', false)
  } finally {
    saving.value = false
  }
}

async function validateRule(row: Row) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}/validate`, {
      method: 'POST',
      body: JSON.stringify({}),
    })
    const payload = await readJson(response)
    activeRow.value = row
    await openReportData(payload.entry)
    flash(payload.message)
  } catch (error) {
    flash(error instanceof Error ? error.message : '校验失败', false)
  }
}

async function openReport(row: Row) {
  activeRow.value = row
  try {
    const detail = await (await request(`${ENDPOINT}/${row.id}`)).json()
    const reportNo = detail.latest_report_no
    if (!reportNo) {
      flash('该规则尚未生成校验报告，请先执行校验规则', false)
      return
    }
    const [reportPayload, historyPayload] = await Promise.all([
      (await request(`${ENDPOINT}/reports/${reportNo}`)).json(),
      (await request(`${ENDPOINT}/${row.id}/reports`)).json(),
    ])
    reportHistory.value = historyPayload.items ?? []
    currentReport.value = reportPayload
    showReport.value = true
  } catch (error) {
    flash(error instanceof Error ? error.message : '报告读取失败', false)
  }
}

async function openReportData(report: ValidationReport) {
  currentReport.value = report
  if (activeRow.value) {
    const historyPayload = await (await request(`${ENDPOINT}/${activeRow.value.id}/reports`)).json()
    reportHistory.value = historyPayload.items ?? []
  }
  showReport.value = true
}

async function loadReport(reportNo: string) {
  const report = await (await request(`${ENDPOINT}/reports/${reportNo}`)).json()
  currentReport.value = report
}

async function revalidate() {
  if (!activeRow.value) return
  busy.value = true
  try {
    const response = await request(`${ENDPOINT}/${activeRow.value.id}/validate`, {
      method: 'POST',
      body: JSON.stringify({}),
    })
    const payload = await readJson(response)
    currentReport.value = payload.entry
    reportHistory.value = (await (await request(`${ENDPOINT}/${activeRow.value.id}/reports`)).json()).items ?? []
    flash(payload.message)
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '复核失败', false)
  } finally {
    busy.value = false
  }
}

async function checkIntegrity(tampered: boolean) {
  if (!currentReport.value) return
  busy.value = true
  try {
    let body: Record<string, any> = {}
    if (tampered) {
      // 模拟错位/丢失/残留：删一个校验项、再塞一个留存报告没有的项
      const cloned = JSON.parse(JSON.stringify(currentReport.value))
      cloned.entry_id = 9999
      cloned.items = cloned.items.filter((item: ReportItem) => item.field !== '分页参数')
      cloned.items.push({ ...cloned.items[0], field: '幽灵残留字段' })
      body = cloned
    }
    const response = await request(`${ENDPOINT}/reports/${currentReport.value.report_no}/integrity`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
    const payload = await response.json()
    const refreshed = await (await request(`${ENDPOINT}/reports/${currentReport.value.report_no}`)).json()
    currentReport.value = refreshed
    flash(payload.message, payload.ok)
  } catch (error) {
    flash(error instanceof Error ? error.message : '完整性核对失败', false)
  } finally {
    busy.value = false
  }
}

async function publishRule(row: Row) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}/publish`, { method: 'POST' })
    const payload = await readJson(response)
    await reload()
    flash(payload.message)
  } catch (error) {
    flash(error instanceof Error ? error.message : '发布失败', false)
  }
}

async function openConflict(row: Row) {
  conflictInfo.value = await (await request(`${ENDPOINT}/${row.id}/conflict`)).json()
  showConflict.value = true
}

async function driveExtraction(row: Row) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}/drive-extraction`, {
      method: 'POST',
      body: JSON.stringify({}),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '驱动数据提取失败')
    executionRecord.value = payload.entry
    showRecord.value = true
    flash(payload.message)
  } catch (error) {
    flash(error instanceof Error ? error.message : '驱动数据提取失败', false)
  }
}

async function openSamples() {
  const payload = await (await request(`${ENDPOINT}/samples`)).json()
  samples.value = payload.items ?? []
  showSamples.value = true
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) throw new Error('选择器规则列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    flash(error instanceof Error ? error.message : '抽取规则列表读取失败', false)
  }
}

onMounted(reload)
</script>
