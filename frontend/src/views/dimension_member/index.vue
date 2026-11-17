<template>
  <section class="page" data-module="dimension_member">
    <header class="page-head">
      <div>
        <h2>数据维度成员映射表</h2>
        <p class="page-desc">
          由分析维度与关联数据集解析层级结构生成，层级路径、数据集引用与维度版本来自同一份映射结果；
          支持按数据集、层级路径与版本定位成员。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="chain-check" type="button" @click="runChainCheck">调用链核对</button>
        <button class="btn" data-testid="sync" type="button" :disabled="syncing" @click="syncMapping">
          {{ syncing ? '同步中…' : '同步映射表' }}
        </button>
        <button class="btn primary" data-testid="export" type="button" @click="exportRows">导出映射清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card" :data-testid="`stat-${item.label}`">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div v-if="chain" class="chain-panel" data-testid="chain-panel" :class="{ 'chain-bad': !chain.ok }">
      <span>调用链核对：{{ chain.in_sync ? '映射表与来源输入一致' : '映射表落后于来源输入，请同步' }}</span>
      <span>生效版本 {{ chain.latest_version ?? '—' }} · 映射行 {{ chain.mapping_row_total }} · 冲突行 {{ chain.conflict_row_total }}</span>
      <span v-if="chain.missing_dataset_refs.length">
        悬空数据集引用：
        <template v-for="ref in chain.missing_dataset_refs" :key="ref.维度编号">
          {{ ref.维度编号 }}→{{ ref.关联数据集 }}；
        </template>
      </span>
      <span v-if="chain.unresolved_dimension_versions.length">
        未解析维度版本：
        <template v-for="ref in chain.unresolved_dimension_versions" :key="ref.维度编号">
          {{ ref.维度版本 }}；
        </template>
      </span>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>关联数据集</span>
        <input v-model="filters.关联数据集" data-testid="filter-关联数据集" placeholder="按数据集名称/编号检索" />
      </label>
      <label class="filter-item">
        <span>层级路径</span>
        <input v-model="filters.层级路径" data-testid="filter-层级路径" placeholder="按维度路径筛选，如 华东/上海" />
      </label>
      <label class="filter-item">
        <span>成员名称</span>
        <input v-model="filters.成员名称" data-testid="filter-成员名称" placeholder="按成员名称检索" />
      </label>
      <label class="filter-item">
        <span>维度编号</span>
        <input v-model="filters.维度编号" data-testid="filter-维度编号" placeholder="如 DIME-0001" />
      </label>
      <label class="filter-item">
        <span>来源输入</span>
        <select v-model="filters.来源输入" data-testid="filter-来源输入">
          <option value="">全部来源</option>
          <option v-for="source in sourceOptions" :key="source" :value="source">{{ source }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>映射版本</span>
        <select v-model="versionFilter" data-testid="filter-版本">
          <option value="">全部版本</option>
          <option v-for="version in versions" :key="version.version_label" :value="version.version_label">
            {{ version.version_label }}（{{ version.status }}）
          </option>
        </select>
      </label>
      <label class="filter-item">
        <span>关键字</span>
        <input v-model="keyword" data-testid="filter-keyword" placeholder="映射编号/成员/路径/维度编号" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div class="version-strip" data-testid="version-strip">
      <span class="strip-title">版本次序：</span>
      <button
        v-for="version in versions"
        :key="version.version_label"
        type="button"
        class="version-chip"
        :class="{ active: version.status === '生效中' }"
        @click="versionFilter = version.version_label; reload()"
      >
        {{ version.version_label }} · {{ version.effective_date }} · {{ version.status }}
        <em v-if="version.conflict_total">冲突 {{ version.conflict_total }}</em>
      </button>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="String(row.id)"
          :data-testid="`row-${row.id}`"
          :data-status="String(row.status)"
          :class="{ 'conflict-row': row.abnormal }"
        >
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" :data-testid="`detail-${row.id}`" @click="openDetail(row)">明细</button>
            <button class="link" type="button" :data-testid="`source-${row.id}`" @click="openSources(row)">另一处来源</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无映射记录，可先同步映射表</td>
        </tr>
      </tbody>
    </table>

    <div v-if="detail" class="modal-backdrop" data-testid="detail-modal" @click.self="detail = null">
      <div class="modal">
        <h3>成员明细 · {{ detail.成员名称 }}</h3>
        <dl class="detail-grid">
          <template v-for="field in detailFields" :key="field">
            <dt>{{ field }}</dt>
            <dd>{{ detail[field] ?? '—' }}</dd>
          </template>
        </dl>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="detail = null">关闭</button>
        </div>
      </div>
    </div>

    <div v-if="sourceView" class="modal-backdrop" data-testid="source-modal" @click.self="sourceView = null">
      <div class="modal">
        <h3>来源输入 · {{ sourceView.member.成员名称 }}</h3>
        <p class="page-desc">
          成员 {{ sourceView.member.成员名称 }} 在 {{ sourceView.member.映射版本 }} 下由两处来源共同确认，
          层级路径与维度版本保持一致。
        </p>
        <table class="data-table">
          <thead>
            <tr><th>来源输入</th><th>映射编号</th><th>层级路径</th><th>维度版本</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in sourceView.sources" :key="item.映射编号">
              <td>{{ item.来源输入 }}</td>
              <td>{{ item.映射编号 }}</td>
              <td>{{ item.层级路径 }}</td>
              <td>{{ item.维度版本 }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="sourceView.dimension_source" class="source-origin">
          <h4>分析维度原始记录</h4>
          <p>{{ sourceView.dimension_source.维度编号 }} · {{ sourceView.dimension_source.维度名称 }} · {{ sourceView.dimension_source.层级结构 }}</p>
        </div>
        <div v-if="sourceView.dataset_source" class="source-origin">
          <h4>关联数据集原始记录</h4>
          <p>{{ sourceView.dataset_source.数据集编号 }} · {{ sourceView.dataset_source.数据集名称 }} · {{ sourceView.dataset_source.来源类型 }}</p>
        </div>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="sourceView = null">关闭</button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条映射记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="actionMessage" class="ok-text">{{ actionMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

const ENDPOINT = '/api/dimension-member'
const columns = [
  '映射编号', '映射版本', '维度编号', '维度名称', '关联数据集', '来源输入',
  '成员名称', '层级深度', '层级路径', '是否叶子', '默认取值', '维度版本',
  '冲突标记', '归属规则', '生效日期',
]
const detailFields = [...columns, '数据集编号']
const sourceOptions = ['分析维度', '关联数据集']

type MappingRow = Record<string, string | number | boolean | null>
type VersionInfo = {
  version_label: string
  version_no: number
  status: string
  effective_date: string
  row_total: number
  conflict_total: number
}
type ChainInfo = {
  ok: boolean
  in_sync: boolean
  latest_version: string | null
  mapping_row_total: number
  conflict_row_total: number
  missing_dataset_refs: { 维度编号: string; 关联数据集: string }[]
  unresolved_dimension_versions: { 维度编号: string; 维度版本: string }[]
}

const rows = ref<MappingRow[]>([])
const total = ref(0)
const versions = ref<VersionInfo[]>([])
const filters = ref<Record<string, string>>({})
const versionFilter = ref('')
const keyword = ref('')
const errorMessage = ref('')
const actionMessage = ref('')
const syncing = ref(false)
const detail = ref<MappingRow | null>(null)
const sourceView = ref<{
  member: Record<string, string>
  sources: Record<string, string>[]
  dimension_source: Record<string, string> | null
  dataset_source: Record<string, string> | null
} | null>(null)
const chain = ref<ChainInfo | null>(null)

const stats = computed(() => [
  { label: '映射记录', value: total.value },
  { label: '映射版本', value: versions.value.length },
  {
    label: '当前生效版本',
    value: versions.value.find((item) => item.status === '生效中')?.version_label ?? '—',
  },
  {
    label: '冲突成员行',
    value: versions.value.find((item) => item.status === '生效中')?.conflict_total ?? 0,
  },
])

function resetFilters() {
  filters.value = {}
  versionFilter.value = ''
  keyword.value = ''
  void reload()
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters.value)) {
    if (value.trim()) params.set(key, value.trim())
  }
  if (versionFilter.value) params.set('version', versionFilter.value)
  if (keyword.value.trim()) params.set('keyword', keyword.value.trim())
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) throw new Error('维度成员映射表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? 0
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '维度成员映射表读取失败'
  }
}

async function reloadVersions() {
  try {
    const response = await request(`${ENDPOINT}/versions`)
    if (!response.ok) return
    versions.value = await response.json()
  } catch {
    versions.value = []
  }
}

async function runChainCheck() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/chain-check`)
    if (!response.ok) throw new Error('调用链核对失败')
    chain.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '调用链核对失败'
  }
}

async function syncMapping() {
  syncing.value = true
  errorMessage.value = ''
  actionMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/sync`, { method: 'POST', body: '{}' })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '同步失败')
    actionMessage.value = payload.message
    await reloadVersions()
    await reload()
    await runChainCheck()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '同步失败'
  } finally {
    syncing.value = false
  }
}

async function exportRows() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters.value)) {
    if (value.trim()) params.set(key, value.trim())
  }
  if (versionFilter.value) params.set('version', versionFilter.value)
  try {
    const response = await request(`${ENDPOINT}/export?${params.toString()}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'dimension_member.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

async function openDetail(row: MappingRow) {
  detail.value = row
}

async function openSources(row: MappingRow) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/sources`)
    if (!response.ok) throw new Error('来源读取失败')
    sourceView.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '来源读取失败'
  }
}

onMounted(async () => {
  await reloadVersions()
  await reload()
  await runChainCheck()
})
</script>

<style scoped>
.chain-panel {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  align-items: center;
  background: #ecfdf3;
  border: 1px solid #abefc6;
  color: #067647;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 13px;
  margin-bottom: 12px;
}
.chain-panel.chain-bad {
  background: #fffbfa;
  border-color: #fda29b;
  color: #b42318;
}
.version-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}
.strip-title {
  font-size: 12px;
  color: #667085;
}
.version-chip {
  border: 1px solid #d0d5dd;
  background: #fff;
  border-radius: 999px;
  padding: 4px 12px;
  font-size: 12px;
  cursor: pointer;
}
.version-chip.active {
  border-color: #1f6feb;
  background: #eff4ff;
  color: #1f6feb;
}
.version-chip em {
  font-style: normal;
  color: #b42318;
  margin-left: 4px;
}
.conflict-row {
  background: #fffbfa;
}
.conflict-row td {
  color: #b42318;
}
.detail-grid {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: 6px 12px;
  margin: 12px 0;
}
.detail-grid dt {
  font-size: 12px;
  color: #667085;
}
.detail-grid dd {
  margin: 0;
  font-size: 13px;
}
.source-origin {
  margin-top: 12px;
  padding: 10px;
  background: #f9fafb;
  border-radius: 8px;
}
.source-origin h4 {
  margin: 0 0 6px;
  font-size: 13px;
}
.source-origin p {
  margin: 0;
  font-size: 12px;
  color: #475467;
}
.ok-text {
  color: #067647;
}
</style>
