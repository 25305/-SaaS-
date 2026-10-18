<!-- H4 API Key 管理页(FR-C1~C6):只显示掩码、创建弹一次、轮换、吊销、单 Key 用量。 -->
<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api, friendlyMessage } from '../api'
import StateWrapper from '../components/StateWrapper.vue'

const loading = ref(false)
const error = ref('')
const keys = ref([])
const usage = ref([])
const message = ref('')
const creating = ref(false)

// 完整 Key 只在创建/轮换的响应里出现一次;关闭后不再可查(FR-C2)
const revealed = ref(null)
const form = reactive({
  name: '',
  quota_monthly_tokens: '',
  quota_monthly_requests: '',
  rate_limit_per_minute: '',
  rate_limit_per_hour: '',
})

function numeric(value) {
  return value === '' || value === null ? undefined : Number(value)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [list, byKey] = await Promise.all([
      api.keys(),
      api.dashboard('month').then((board) => board.by_key).catch(() => []),
    ])
    keys.value = list
    usage.value = byKey
  } catch (err) {
    error.value = friendlyMessage(err)
  } finally {
    loading.value = false
  }
}

function usageOf(keyId) {
  return usage.value.find((row) => row.key_id === keyId) || null
}

async function create() {
  message.value = ''
  if (!form.name.trim()) {
    message.value = '请填写 Key 用途描述'
    return
  }
  creating.value = true
  try {
    const created = await api.createKey({
      name: form.name.trim(),
      quota_monthly_tokens: numeric(form.quota_monthly_tokens),
      quota_monthly_requests: numeric(form.quota_monthly_requests),
      rate_limit_per_minute: numeric(form.rate_limit_per_minute),
      rate_limit_per_hour: numeric(form.rate_limit_per_hour),
    })
    revealed.value = created.key
    message.value = created.warning
    Object.assign(form, {
      name: '', quota_monthly_tokens: '', quota_monthly_requests: '',
      rate_limit_per_minute: '', rate_limit_per_hour: '',
    })
    await load()
  } catch (err) {
    message.value = friendlyMessage(err)
  } finally {
    creating.value = false
  }
}

async function rotate(key) {
  message.value = ''
  try {
    const rotated = await api.rotateKey(key.id)
    revealed.value = rotated.key
    message.value = `已轮换。旧 Key(${rotated.old_key.masked})将在 ${rotated.old_key.expire_at} 前失效,请在此窗口内完成切换。`
    await load()
  } catch (err) {
    message.value = friendlyMessage(err)
  }
}

async function revoke(key) {
  message.value = ''
  if (!window.confirm(`确认吊销 ${key.masked}?吊销后该 Key 的调用会立即失败。`)) return
  try {
    await api.revokeKey(key.id)
    message.value = 'API Key 已吊销,调用立即失效'
    await load()
  } catch (err) {
    message.value = friendlyMessage(err)
  }
}

async function copy(value) {
  try {
    await navigator.clipboard.writeText(value)
    message.value = '已复制到剪贴板'
  } catch {
    message.value = '复制失败,请手动选中复制'
  }
}

function statusText(status) {
  return { active: '启用中', expiring: '即将过期', revoked: '已吊销' }[status] || status
}

onMounted(load)
</script>

<template>
  <section class="page">
    <div class="toolbar">
      <span>共 {{ keys.length }} 把 Key</span>
      <button class="primary" @click="load">刷新</button>
    </div>

    <StateWrapper :loading="loading" :error="error" :is-empty="!keys.length"
                  empty-text="还没有 API Key,请在下方创建第一把" loading-text="正在加载 Key 列表…"
                  @retry="load">
      <div class="panel">
        <table>
          <thead>
            <tr>
              <th>名称</th><th>Key</th><th>状态</th><th>本月请求</th><th>本月 Token</th>
              <th>最后使用</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <!-- FR-C2:列表永远只显示掩码,不返回完整值 -->
            <tr v-for="key in keys" :key="key.id">
              <td>{{ key.name }}</td>
              <td><code>{{ key.masked }}</code></td>
              <td>
                <span :class="['badge', key.status === 'active' ? 'badge-ok' : 'badge-off']">
                  {{ statusText(key.status) }}
                </span>
              </td>
              <td>{{ usageOf(key.id)?.requests ?? 0 }}</td>
              <td>{{ (usageOf(key.id)?.total_tokens ?? 0).toLocaleString('zh-CN') }}</td>
              <td>{{ key.last_used_at ? key.last_used_at.replace('T', ' ').slice(0, 19) : '从未使用' }}</td>
              <td>
                <button @click="rotate(key)">轮换</button>
                <button @click="revoke(key)" :disabled="key.status === 'revoked'">吊销</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </StateWrapper>

    <div class="panel">
      <h3>创建 API Key</h3>
      <p class="hint">配额留空表示继承租户配额;完整 Key 仅在创建成功后显示一次。</p>
      <div class="form-grid">
        <label>用途描述<input v-model="form.name" placeholder="官网问答机器人" /></label>
        <label>月度 Token 上限<input v-model="form.quota_monthly_tokens" type="number" placeholder="继承租户" /></label>
        <label>月度请求上限<input v-model="form.quota_monthly_requests" type="number" placeholder="继承租户" /></label>
        <label>每分钟限流<input v-model="form.rate_limit_per_minute" type="number" placeholder="不限" /></label>
        <label>每小时限流<input v-model="form.rate_limit_per_hour" type="number" placeholder="不限" /></label>
      </div>
      <button class="primary" :disabled="creating" @click="create">
        {{ creating ? '创建中…' : '创建 Key' }}
      </button>
    </div>

    <div v-if="revealed" class="key-reveal" role="dialog">
      <h3>请立即保存你的 API Key</h3>
      <p class="error">关闭后将无法再次查看完整值。</p>
      <code class="full-key">{{ revealed }}</code>
      <div>
        <button class="primary" @click="copy(revealed)">复制</button>
        <button @click="revealed = null">我已保存</button>
      </div>
    </div>

    <p v-if="message" :class="message.includes('失败') || message.includes('请') ? 'error' : 'hint'">
      {{ message }}
    </p>
  </section>
</template>
