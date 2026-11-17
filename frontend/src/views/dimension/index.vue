<template>
  <section class="page" data-module="dimension">
    <header class="page-head">
      <div>
        <h2>维度管理管理</h2>
        <p class="page-desc">维护分析维度，围绕维度编号、维度名称、维度类型、关联数据集做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记分析维度</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出维度管理清单</button>
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
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
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
          <td :colspan="columns.length + 1" class="empty-state">暂无维度管理数据，可先登记分析维度</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记分析维度</h3>
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

    <section class="member-panel" data-testid="member-panel">
      <header class="member-panel-head">
        <div>
          <h3>维度成员检索</h3>
          <p class="page-desc">按数据集与层级路径检索数据维度成员映射表，定位成员当前归属路径与被引用的维度版本（默认锁定最新生效版本）。</p>
        </div>
        <RouterLink class="btn" to="/dimension_member">打开映射表</RouterLink>
      </header>
      <form class="filter-bar" @submit.prevent="searchMembers">
        <label class="filter-item">
          <span>关联数据集</span>
          <input v-model="memberQuery.dataset" data-testid="member-filter-dataset" placeholder="数据集名称或编号" />
        </label>
        <label class="filter-item">
          <span>维度路径</span>
          <input v-model="memberQuery.path" data-testid="member-filter-path" placeholder="如 华东/上海" />
        </label>
        <label class="filter-item">
          <span>成员名称</span>
          <input v-model="memberQuery.member" data-testid="member-filter-member" placeholder="成员名称关键字" />
        </label>
        <button class="btn primary" data-testid="member-query" type="submit">检索成员</button>
        <button class="btn ghost" type="button" data-testid="member-reset" @click="resetMemberQuery">重置</button>
      </form>
      <table v-if="memberRows.length" class="data-table">
        <thead>
          <tr>
            <th v-for="column in memberColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in memberRows" :key="`${row.映射编号}-${row.来源输入}`" :class="{ 'conflict-row': row.abnormal }">
            <td v-for="column in memberColumns" :key="column">{{ row[column] ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else-if="memberSearched" class="empty-state" data-testid="member-empty">当前条件下没有定位到维度成员</p>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条维度管理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/dimension'
const columns = ["维度编号", "维度名称", "维度类型", "关联数据集", "层级结构", "默认取值", "排序规则", "维度状态"]
const actions = ["提交定义", "发布维度", "停用维度"]
const statuses = ["草稿", "已定义", "已发布", "已停用"]
const metricNames = ["已发布维度", "草稿维度", "停用维度"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})

const memberColumns = ['映射版本', '关联数据集', '来源输入', '成员名称', '层级路径', '默认取值', '维度版本', '冲突标记']
const memberQuery = ref<Record<string, string>>({ dataset: '', path: '', member: '' })
const memberRows = ref<Row[]>([])
const memberSearched = ref(false)
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

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
    link.download = 'dimension.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
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
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
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
    if (!response.ok) {
      throw new Error('维度管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '维度管理操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('分析维度列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '维度管理列表读取失败'
  }
}

async function searchMembers() {
  errorMessage.value = ''
  memberSearched.value = true
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(memberQuery.value)) {
    if (value.trim()) params.set(key, value.trim())
  }
  try {
    const response = await request(`/api/dimension-member/lookup?${params.toString()}`)
    if (!response.ok) throw new Error('维度成员检索失败')
    memberRows.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '维度成员检索失败'
    memberRows.value = []
  }
}

function resetMemberQuery() {
  memberQuery.value = { dataset: '', path: '', member: '' }
  memberRows.value = []
  memberSearched.value = false
}

onMounted(reload)
</script>

<style scoped>
.member-panel {
  margin-top: 18px;
  background: #fff;
  border: 1px solid #d0d5dd;
  border-radius: 10px;
  padding: 14px 16px;
}
.member-panel-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.member-panel-head h3 {
  margin: 0 0 4px;
  font-size: 15px;
}
.conflict-row {
  background: #fffbfa;
}
.conflict-row td {
  color: #b42318;
}
</style>
