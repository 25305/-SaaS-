<script setup>
import { ref, computed } from 'vue'
const pages = ['登录', '租户管理台', '工作台', 'Key 管理', '问答体验', '账单']
const current = ref('工作台')
const tenant = ref('tenant-a')
const loading = ref(false)
const error = ref('')
const metrics = ref({ total_requests: 0, prompt_tokens: 0, completion_tokens: 0, avg_latency_ms: 0, p95_latency_ms: 0, cache_hit_rate: 0 })
const hasData = computed(() => metrics.value.total_requests > 0)
async function refresh() {
  loading.value = true; error.value = ''
  try { const response = await fetch(`/api/usage/dashboard?days=30`); if (!response.ok) throw new Error('加载失败'); metrics.value = (await response.json()).data.summary }
  catch (e) { error.value = e.message }
  finally { loading.value = false }
}
function exportBill(type) { window.open(`/api/usage/billing/export${type === 'xlsx' ? '.xlsx' : ''}?days=30`, '_blank') }
</script>

<template>
  <div class="layout">
    <aside><h1>Smart SaaS</h1><button v-for="page in pages" :key="page" :class="{active: current === page}" @click="current = page">{{ page }}</button></aside>
    <main><header><span>成员 D 演示控制台</span><select v-model="tenant" @change="refresh"><option>tenant-a</option><option>tenant-b</option><option>tenant-c</option></select></header>
      <section v-if="current === '工作台'" class="content"><h2>用量工作台</h2><button @click="refresh">刷新数据</button><p v-if="loading">加载中...</p><p v-else-if="error" class="error">{{ error }}</p><p v-else-if="!hasData" class="empty">暂无数据</p><div v-else class="cards"><article v-for="(value, key) in metrics" :key="key"><small>{{ key }}</small><strong>{{ value }}</strong></article></div></section>
      <section v-else-if="current === '账单'" class="content"><h2>账单导出</h2><p>当前租户：{{ tenant }}</p><button @click="exportBill('csv')">导出 CSV</button><button @click="exportBill('xlsx')">导出 Excel</button></section>
      <section v-else class="content"><h2>{{ current }}</h2><p class="empty">页面骨架已就绪，等待团队接口接入。</p></section>
    </main>
  </div>
</template>
