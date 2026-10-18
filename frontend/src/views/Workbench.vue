<!-- H3 工作台(租户视角):用量卡片 + 趋势/饼图/延迟分布 + 最近请求(FR-F1/F2/F3、FR-H3)。 -->
<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, friendlyMessage } from '../api'
import StateWrapper from '../components/StateWrapper.vue'
import TrendLine from '../components/charts/TrendLine.vue'
import KeyPie from '../components/charts/KeyPie.vue'
import LatencyBar from '../components/charts/LatencyBar.vue'

const range = ref('month')
const custom = reactive({ start: '', end: '' })
const loading = ref(false)
const error = ref('')
const board = ref(null)
const quota = ref(null)
const records = ref([])

// 指标卡片的展示顺序与中文名(FR-F1 要求指标齐全,顺序固定便于对照验收)
const METRICS = [
  ['total_requests', '请求总数', 'number'],
  ['success', '成功', 'number'],
  ['errors', '失败', 'number'],
  ['error_rate', '错误率', 'percent'],
  ['prompt_tokens', '输入 Token', 'number'],
  ['completion_tokens', '输出 Token', 'number'],
  ['avg_latency_ms', '平均延迟', 'ms'],
  ['p95_latency_ms', 'P95 延迟', 'ms'],
  ['cache_hit_rate', '缓存命中率', 'percent'],
  ['quota_rejected', '配额拦截次数', 'number'],
]

const summary = computed(() => board.value?.summary || {})
const trend = computed(() => board.value?.trend || [])
const byKey = computed(() => board.value?.by_key || [])
const distribution = computed(() => board.value?.latency_distribution || [])

// 空态口径:区间内一次请求都没有(而不是"指标全为 0 也强行画图")
const isEmpty = computed(() => {
  const s = summary.value
  return Boolean(s) && (s.total_requests || 0) === 0 && (s.quota_rejected || 0) === 0
})

function formatValue(value, kind) {
  if (value === undefined || value === null) return '—'
  if (kind === 'percent') return `${(value * 100).toFixed(2)}%`
  if (kind === 'ms') return `${value} ms`
  return value.toLocaleString('zh-CN')
}

function operatorText(statusCode) {
  return statusCode >= 200 && statusCode < 400 ? '成功' : '异常'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const query = range.value === 'custom' ? `custom&start=${custom.start}&end=${custom.end}` : range.value
    const [boardData, quotaData, recent] = await Promise.all([
      api.dashboard(query),
      api.quotaStatus().catch(() => null),
      api.recentRecords(20).catch(() => []),
    ])
    board.value = boardData
    quota.value = quotaData
    records.value = recent || []
  } catch (err) {
    error.value = friendlyMessage(err)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="page">
    <div class="toolbar">
      <div class="segmented">
        <button v-for="option in [['today', '今日'], ['week', '本周'], ['month', '本月'], ['custom', '自定义']]"
                :key="option[0]" :class="{ active: range === option[0] }"
                @click="range = option[0]; option[0] !== 'custom' && load()">
          {{ option[1] }}
        </button>
      </div>
      <template v-if="range === 'custom'">
        <input v-model="custom.start" type="date" aria-label="开始日期" />
        <input v-model="custom.end" type="date" aria-label="结束日期" />
      </template>
      <button class="primary" @click="load">刷新数据</button>
      <span v-if="summary.summary_updated_at" class="hint">
        汇总数据更新于 {{ summary.summary_updated_at.replace('T', ' ').slice(0, 19) }}
      </span>
    </div>

    <StateWrapper :loading="loading" :error="error" :is-empty="isEmpty"
                  empty-text="该时间区间内还没有用量数据,完成一次问答调用后即可看到指标与趋势。"
                  loading-text="正在加载看板指标…" @retry="load">
      <div class="metric-grid">
        <article v-for="item in METRICS" :key="item[0]">
          <small>{{ item[1] }}</small>
          <strong>{{ formatValue(summary[item[0]], item[2]) }}</strong>
        </article>
      </div>

      <div class="panel quota-panel" v-if="quota">
        <h3>本周期配额</h3>
        <p>
          Token：{{ quota.used_tokens.toLocaleString('zh-CN') }} /
          {{ quota.limit_tokens === null ? '不限' : quota.limit_tokens.toLocaleString('zh-CN') }} ·
          请求：{{ quota.used_requests.toLocaleString('zh-CN') }} /
          {{ quota.limit_requests === null ? '不限' : quota.limit_requests.toLocaleString('zh-CN') }}
        </p>
        <p class="hint">重置时间：{{ quota.reset_at }} · 超额策略：{{ quota.policy }}</p>
      </div>

      <div class="dashboard-grid">
        <div class="chart-card">
          <h3>请求趋势</h3>
          <StateWrapper :is-empty="!trend.length" empty-text="区间内没有可绘制的趋势数据">
            <TrendLine :trend="trend" />
          </StateWrapper>
        </div>

        <div class="chart-card">
          <h3>Key 消耗占比</h3>
          <StateWrapper :is-empty="!byKey.length" empty-text="区间内没有 Key 维度的消耗数据">
            <KeyPie :by-key="byKey" />
          </StateWrapper>
        </div>

        <div class="chart-card">
          <h3>延迟分布</h3>
          <StateWrapper :is-empty="!distribution.length" empty-text="区间内没有延迟样本">
            <LatencyBar :distribution="distribution" />
          </StateWrapper>
        </div>
      </div>

      <div class="panel">
        <h3>最近请求</h3>
        <StateWrapper :is-empty="!records.length" empty-text="还没有访问记录">
          <table>
            <thead>
              <tr><th>时间</th><th>方法</th><th>路径</th><th>状态码</th><th>耗时</th></tr>
            </thead>
            <tbody>
              <!-- FR-H7:v-for 必须带业务唯一 key,不用 index -->
              <tr v-for="record in records" :key="record.id">
                <td>{{ record.created_at.replace('T', ' ').slice(0, 19) }}</td>
                <td>{{ record.method }}</td>
                <td><code>{{ record.path }}</code></td>
                <td>{{ record.status_code }} · {{ operatorText(record.status_code) }}</td>
                <td>{{ record.latency_ms }} ms</td>
              </tr>
            </tbody>
          </table>
        </StateWrapper>
      </div>
    </StateWrapper>
  </section>
</template>
