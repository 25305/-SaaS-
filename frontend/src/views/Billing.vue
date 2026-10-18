<!-- H6 账单页:周期选择 + 按日汇总 + 流水明细 + 导出 + 可追溯校验(FR-F4/F5、FR-H6)。 -->
<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, download, friendlyMessage } from '../api'
import StateWrapper from '../components/StateWrapper.vue'

const period = ref('month')
const customPeriod = ref('')
const loading = ref(false)
const error = ref('')
const exporting = ref('')
const bill = ref(null)
const daily = ref([])
const detail = ref([])
const verification = ref(null)
const notice = ref('')

const PERIOD_OPTIONS = [
  ['today', '今日'], ['week', '本周'], ['month', '本月'],
]

const effectivePeriod = computed(() =>
  period.value === 'custom' ? customPeriod.value || 'month' : period.value)

const TOTALS = [
  ['total_requests', '请求次数'],
  ['total_prompt_tokens', '输入 Token'],
  ['total_completion_tokens', '输出 Token'],
  ['total_tokens', '合计 Token'],
  ['cache_hits', '缓存命中'],
  ['avg_latency_ms', '平均延迟(ms)'],
  ['p95_latency_ms', 'P95 延迟(ms)'],
]

async function load() {
  loading.value = true
  error.value = ''
  verification.value = null
  notice.value = ''
  try {
    const target = effectivePeriod.value
    const [summary, days, rows] = await Promise.all([
      api.billingSummary(target),
      api.billingDaily(target),
      api.billingDetail(target, 100),
    ])
    bill.value = summary
    daily.value = days
    detail.value = rows
  } catch (err) {
    error.value = friendlyMessage(err)
  } finally {
    loading.value = false
  }
}

async function exportBill(fmt) {
  exporting.value = fmt
  notice.value = ''
  try {
    const target = effectivePeriod.value
    await download(`/api/v1/billing/export?period=${target}&fmt=${fmt}`,
                   `bill_${target}.${fmt}`)
    notice.value = `已导出 ${fmt.toUpperCase()} 账单`
  } catch (err) {
    notice.value = friendlyMessage(err)
  } finally {
    exporting.value = ''
  }
}

async function verify() {
  notice.value = ''
  try {
    // 把"抽查 5 行"变成自动化断言:账单每个数字都能在流水中找到来源(FR-F5)
    verification.value = await api.billingVerify(effectivePeriod.value)
  } catch (err) {
    notice.value = friendlyMessage(err)
  }
}

onMounted(load)
</script>

<template>
  <section class="page">
    <div class="toolbar">
      <div class="segmented">
        <button v-for="option in PERIOD_OPTIONS" :key="option[0]"
                :class="{ active: period === option[0] }"
                @click="period = option[0]; load()">{{ option[1] }}</button>
        <button :class="{ active: period === 'custom' }" @click="period = 'custom'">指定月份</button>
      </div>
      <input v-if="period === 'custom'" v-model="customPeriod" type="month"
             aria-label="计费月份" @change="load" />
      <button class="primary" @click="load">刷新</button>
      <button :disabled="exporting === 'csv'" @click="exportBill('csv')">导出 CSV</button>
      <button :disabled="exporting === 'xlsx'" @click="exportBill('xlsx')">导出 Excel</button>
      <button @click="verify">可追溯校验</button>
    </div>

    <StateWrapper :loading="loading" :error="error"
                  :is-empty="Boolean(bill) && !bill.total_requests"
                  empty-text="该周期没有产生费用,无需出账" loading-text="正在加载账单…"
                  @retry="load">
      <div v-if="bill" class="metric-grid">
        <article v-for="item in TOTALS" :key="item[0]">
          <small>{{ item[1] }}</small>
          <strong>{{ (bill[item[0]] ?? 0).toLocaleString('zh-CN') }}</strong>
        </article>
      </div>

      <p v-if="bill" class="hint">
        计费区间 {{ bill.period.start }} ~ {{ bill.period.end }} · 生成时间 {{ bill.generated_at.slice(0, 19).replace('T', ' ') }}
      </p>

      <div v-if="verification" class="panel">
        <h3>可追溯校验</h3>
        <p :class="verification.traceable ? 'hint' : 'error'">
          {{ verification.traceable
            ? '通过:账单每项数字都能在流水明细中找到对应记录。'
            : '未通过,存在对不上的项目。' }}
        </p>
        <table class="meta-table">
          <tbody>
            <tr v-for="(value, name) in verification.checked" :key="name">
              <th>{{ name }}</th><td>{{ value.toLocaleString('zh-CN') }}</td>
            </tr>
          </tbody>
        </table>
        <ul v-if="Object.keys(verification.mismatches).length">
          <li v-for="(pair, name) in verification.mismatches" :key="name" class="error">
            {{ name }}:账单 {{ pair.bill }} vs 流水 {{ pair.detail }}
          </li>
        </ul>
      </div>

      <div class="panel">
        <h3>按日汇总</h3>
        <StateWrapper :is-empty="!daily.length" empty-text="该周期没有按日汇总数据">
          <table>
            <thead>
              <tr><th>日期</th><th>请求</th><th>成功</th><th>失败</th><th>输入 Token</th>
                <th>输出 Token</th><th>平均延迟</th><th>P95</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in daily" :key="row.date">
                <td>{{ row.date }}</td>
                <td>{{ row.requests }}</td>
                <td>{{ row.success }}</td>
                <td>{{ row.errors }}</td>
                <td>{{ row.prompt_tokens.toLocaleString('zh-CN') }}</td>
                <td>{{ row.completion_tokens.toLocaleString('zh-CN') }}</td>
                <td>{{ row.avg_latency_ms }} ms</td>
                <td>{{ row.p95_latency_ms }} ms</td>
              </tr>
            </tbody>
          </table>
        </StateWrapper>
      </div>

      <div class="panel">
        <h3>流水明细(最近 100 条,均带 request_id 可逐行回查)</h3>
        <StateWrapper :is-empty="!detail.length" empty-text="该周期没有流水明细">
          <table>
            <thead>
              <tr><th>时间</th><th>request_id</th><th>Key</th><th>模型</th><th>状态</th>
                <th>输入</th><th>输出</th><th>延迟</th><th>缓存</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in detail" :key="row.id">
                <td>{{ row.created_at.replace('T', ' ').slice(0, 19) }}</td>
                <td><code>{{ row.request_id }}</code></td>
                <td>{{ row.key_id ?? 'jwt' }}</td>
                <td>{{ row.model }}</td>
                <td>{{ row.status }}</td>
                <td>{{ row.prompt_tokens }}</td>
                <td>{{ row.completion_tokens }}</td>
                <td>{{ row.latency_ms }} ms</td>
                <td>{{ row.cache_hit ? '命中' : '—' }}</td>
              </tr>
            </tbody>
          </table>
        </StateWrapper>
      </div>
    </StateWrapper>

    <p v-if="notice" class="hint">{{ notice }}</p>
  </section>
</template>
