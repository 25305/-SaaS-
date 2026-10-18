<!-- H2 租户管理台(平台管理员):租户列表、创建/停用、配额设置、全局用量概览(FR-A2、FR-H2、FR-F2)。 -->
<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, friendlyMessage } from '../api'
import StateWrapper from '../components/StateWrapper.vue'

const loading = ref(false)
const error = ref('')
const tenants = ref([])
const message = ref('')
const creating = ref(false)
const form = reactive({
  code: '', name: '', admin_username: '', admin_password: '',
})

// 全局用量概览(FR-A2/FR-F2):平台管理员视角,跨租户合计
const usageRange = ref('month')
const usageLoading = ref(false)
const usage = ref(null)

// 配额设置(FR-D1/FR-D6):按租户内联编辑
const editingId = ref(null)
const savingQuota = ref(false)
const quotaForm = reactive({ quota_monthly_tokens: '', quota_monthly_requests: '', overage_policy: 'reject' })

const activeCount = computed(() => tenants.value.filter((t) => t.is_active).length)

const usageByTenant = computed(() => {
  const map = {}
  for (const row of usage.value?.by_tenant || []) map[row.tenant_id] = row
  return map
})

function pct(value) {
  return value == null ? '-' : `${(value * 100).toFixed(1)}%`
}

function policyLabel(policy) {
  return policy === 'soft_warn' ? '软超额告警' : '直接拒绝'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    tenants.value = await api.tenants()
  } catch (err) {
    error.value = friendlyMessage(err)
  } finally {
    loading.value = false
  }
}

async function loadUsage() {
  usageLoading.value = true
  try {
    usage.value = await api.globalUsage(usageRange.value)
  } catch (err) {
    message.value = friendlyMessage(err)
  } finally {
    usageLoading.value = false
  }
}

async function create() {
  message.value = ''
  if (!form.code.trim() || !form.name.trim() || !form.admin_username.trim()) {
    message.value = '租户编码、名称与管理员用户名均为必填'
    return
  }
  if (form.admin_password.length < 8) {
    message.value = '管理员密码至少 8 位'
    return
  }
  creating.value = true
  try {
    await api.createTenant({ ...form })
    message.value = '租户创建成功,其首个管理员已可登录'
    Object.assign(form, { code: '', name: '', admin_username: '', admin_password: '' })
    await Promise.all([load(), loadUsage()])
  } catch (err) {
    message.value = friendlyMessage(err)
  } finally {
    creating.value = false
  }
}

async function toggle(tenant) {
  message.value = ''
  try {
    await api.setTenantActive(tenant.id, !tenant.is_active)
    await Promise.all([load(), loadUsage()])
  } catch (err) {
    message.value = friendlyMessage(err)
  }
}

function startEditQuota(tenant) {
  message.value = ''
  editingId.value = tenant.id
  // 空字符串代表"不限制",保存时转回 null
  quotaForm.quota_monthly_tokens = tenant.quota_monthly_tokens ?? ''
  quotaForm.quota_monthly_requests = tenant.quota_monthly_requests ?? ''
  quotaForm.overage_policy = tenant.overage_policy || 'reject'
}

function cancelEditQuota() {
  editingId.value = null
}

async function saveQuota(tenant) {
  message.value = ''
  savingQuota.value = true
  try {
    await api.setTenantQuota(tenant.id, {
      quota_monthly_tokens: quotaForm.quota_monthly_tokens === '' ? null : Number(quotaForm.quota_monthly_tokens),
      quota_monthly_requests: quotaForm.quota_monthly_requests === '' ? null : Number(quotaForm.quota_monthly_requests),
      overage_policy: quotaForm.overage_policy,
    })
    message.value = '租户配额已更新'
    editingId.value = null
    await Promise.all([load(), loadUsage()])
  } catch (err) {
    message.value = friendlyMessage(err)
  } finally {
    savingQuota.value = false
  }
}

onMounted(() => {
  load()
  loadUsage()
})
</script>

<template>
  <section class="page">
    <!-- 全局用量概览(FR-A2/FR-F2) -->
    <div class="panel">
      <div class="toolbar" style="justify-content: space-between; margin-bottom: 10px">
        <h3 style="margin: 0">全局用量概览</h3>
        <div class="segmented">
          <button :class="{ active: usageRange === 'today' }" @click="usageRange = 'today'; loadUsage()">今日</button>
          <button :class="{ active: usageRange === 'week' }" @click="usageRange = 'week'; loadUsage()">本周</button>
          <button :class="{ active: usageRange === 'month' }" @click="usageRange = 'month'; loadUsage()">本月</button>
        </div>
      </div>
      <p class="hint">平台管理员视角,跨租户合计。标量指标只读汇总表,接口耗时恒定(FR-F6)。</p>
      <div v-if="usage" class="metric-grid">
        <article><small>租户数</small><strong>{{ usage.summary.tenant_count }}</strong></article>
        <article><small>总请求数</small><strong>{{ usage.summary.total_requests }}</strong></article>
        <article><small>错误率</small><strong>{{ pct(usage.summary.error_rate) }}</strong></article>
        <article><small>Token 合计</small><strong>{{ usage.summary.total_tokens }}</strong></article>
        <article><small>缓存命中率</small><strong>{{ pct(usage.summary.cache_hit_rate) }}</strong></article>
        <article><small>平均延迟</small><strong>{{ usage.summary.avg_latency_ms }} ms</strong></article>
      </div>
      <p v-else-if="usageLoading" class="hint">正在加载全局用量…</p>
    </div>

    <div class="toolbar" style="margin-top: 24px">
      <span>共 {{ tenants.length }} 个租户 · 启用中 {{ activeCount }}</span>
      <button class="primary" @click="load">刷新</button>
    </div>

    <StateWrapper :loading="loading" :error="error" :is-empty="!tenants.length"
                  empty-text="还没有任何租户" loading-text="正在加载租户列表…" @retry="load">
      <div class="panel">
        <table>
          <thead>
            <tr>
              <th>ID</th><th>编码</th><th>名称</th><th>状态</th><th>成员数</th>
              <th>区间请求</th><th>区间 Token</th><th>Token 配额</th><th>超额策略</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="tenant in tenants" :key="tenant.id">
              <tr>
                <td>{{ tenant.id }}</td>
                <td><code>{{ tenant.code }}</code></td>
                <td>{{ tenant.name }}</td>
                <td>
                  <span :class="['badge', tenant.is_active ? 'badge-ok' : 'badge-off']">
                    {{ tenant.is_active ? '启用中' : '已停用' }}
                  </span>
                </td>
                <td>{{ tenant.user_count }}</td>
                <td>{{ usageByTenant[tenant.id]?.requests ?? 0 }}</td>
                <td>{{ usageByTenant[tenant.id]?.total_tokens ?? 0 }}</td>
                <td>{{ tenant.quota_monthly_tokens == null ? '不限' : tenant.quota_monthly_tokens }}</td>
                <td>{{ policyLabel(tenant.overage_policy) }}</td>
                <td>
                  <button @click="startEditQuota(tenant)">配额设置</button>
                  <!-- 平台租户是超管自身所在租户,后端也禁止停用,前端一并禁用避免无谓报错 -->
                  <button :disabled="tenant.code === 'platform'" @click="toggle(tenant)">
                    {{ tenant.is_active ? '停用' : '启用' }}
                  </button>
                </td>
              </tr>
              <!-- 内联配额编辑行(FR-D1/FR-D6) -->
              <tr v-if="editingId === tenant.id">
                <td colspan="10">
                  <div class="form-grid" style="margin: 10px 0">
                    <label>月度 Token 上限
                      <input v-model="quotaForm.quota_monthly_tokens" type="number" min="0" placeholder="留空表示不限制" />
                    </label>
                    <label>月度请求次数上限
                      <input v-model="quotaForm.quota_monthly_requests" type="number" min="0" placeholder="留空表示不限制" />
                    </label>
                    <label>超额策略
                      <select v-model="quotaForm.overage_policy" style="display:block;width:100%;margin-top:8px;padding:11px;background:#0d1b2a;color:#fff;border:1px solid #38546f">
                        <option value="reject">直接拒绝(429)</option>
                        <option value="soft_warn">软超额告警(放行 + 告警)</option>
                      </select>
                    </label>
                  </div>
                  <button class="primary" :disabled="savingQuota" @click="saveQuota(tenant)">
                    {{ savingQuota ? '保存中…' : '保存配额' }}
                  </button>
                  <button @click="cancelEditQuota">取消</button>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </StateWrapper>

    <div class="panel" style="margin-top: 24px">
      <h3>创建租户</h3>
      <p class="hint">创建租户会同时创建其首个管理员账号,新租户开箱即可登录。</p>
      <div class="form-grid">
        <label>租户编码<input v-model="form.code" placeholder="jwc" /></label>
        <label>租户名称<input v-model="form.name" placeholder="教务处" /></label>
        <label>管理员用户名<input v-model="form.admin_username" placeholder="jwc_admin" /></label>
        <label>管理员密码<input v-model="form.admin_password" type="password" placeholder="至少 8 位" /></label>
      </div>
      <button class="primary" :disabled="creating" @click="create">
        {{ creating ? '创建中…' : '创建租户' }}
      </button>
      <p v-if="message" :class="message.includes('成功') || message.includes('已更新') ? 'hint' : 'error'">{{ message }}</p>
    </div>
  </section>
</template>
