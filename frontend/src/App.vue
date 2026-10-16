<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const router = useRouter(), route = useRoute()
const token = ref(localStorage.getItem('smartqa_token') || '')
const tenant = ref('academic'), loading = ref(false), error = ref('')
const question = ref('牛顿第二定律是什么？'), answer = ref('')
const dashboard = ref({ summary: {}, by_key: [], trend: [] })
const nav = [['tenant', '租户管理'], ['dashboard', '工作台'], ['keys', 'Key 管理'], ['qa', '问答体验'], ['billing', '账单导出']]
const title = computed(() => ({ login: '登录', tenant: '租户管理台', dashboard: '用量工作台', keys: 'API Key 管理', qa: '问答体验', billing: '账单导出' }[route.name] || '智能问答 SaaS'))
const hasData = computed(() => Boolean(dashboard.value.summary?.total_requests))
const headers = () => ({ Authorization: `Bearer ${token.value}`, 'Content-Type': 'application/json' })
const go = (name) => router.push({ name })
function login() { if (!token.value.trim()) return error.value = '请粘贴登录接口返回的 JWT Token'; localStorage.setItem('smartqa_token', token.value.trim()); go('dashboard') }
function logout() { localStorage.removeItem('smartqa_token'); token.value = ''; go('login') }
async function refresh() { loading.value = true; error.value = ''; try { const r = await fetch('/api/usage/dashboard?days=30', { headers: headers() }); const b = await r.json(); if (!r.ok) throw new Error(b.message || '看板加载失败'); dashboard.value = b.data } catch (e) { error.value = e.message } finally { loading.value = false } }
async function ask() { loading.value = true; error.value = ''; answer.value = ''; try { const r = await fetch('/api/v1/qa/ask', { method: 'POST', headers: headers(), body: JSON.stringify({ question: question.value }) }); const b = await r.json(); if (!r.ok) throw new Error(b.message || '问答失败'); answer.value = b.data?.answer || b.answer || '' } catch (e) { error.value = e.message } finally { loading.value = false } }
function exportBill(type) { window.open(`/api/usage/billing/export${type === 'xlsx' ? '.xlsx' : ''}?days=30`, '_blank') }
</script>

<template>
  <main v-if="route.name === 'login'" class="login-page"><section class="login-card"><p class="eyebrow">SMARTQA / TENANT OPS</p><h1>进入智能问答<br><em>SaaS 管理台</em></h1><p>使用后端登录接口获得的 JWT Token 进入工作区。</p><input v-model="token" placeholder="粘贴 JWT Token" @keyup.enter="login" /><button class="primary" @click="login">进入工作台 →</button><p v-if="error" class="error">{{ error }}</p></section></main>
  <div v-else class="shell"><aside><div class="brand"><b>Q</b><span>SMART<br>QA</span></div><nav><button v-for="item in nav" :key="item[0]" :class="{active: route.name === item[0]}" @click="go(item[0])">{{ item[1] }}</button></nav><button class="logout" @click="logout">退出登录</button></aside>
    <main class="workspace"><header><div><p class="eyebrow">TENANT / {{ tenant.toUpperCase() }}</p><h2>{{ title }}</h2></div><select v-model="tenant"><option>academic</option><option>physics</option><option>platform</option></select></header>
      <section v-if="route.name === 'dashboard'" class="page"><div class="toolbar"><button class="primary" @click="refresh">刷新数据</button><span>最近 30 天</span></div><p v-if="loading" class="state">正在加载指标…</p><p v-else-if="error" class="error state">{{ error }}</p><template v-else><p v-if="!hasData" class="state">暂无用量数据，完成一次问答调用后会显示趋势。</p><div v-else class="metric-grid"><article v-for="(value,key) in dashboard.summary" :key="key"><small>{{ key }}</small><strong>{{ value }}</strong></article></div><div class="chart-card"><h3>请求趋势</h3><div class="bars"><i v-for="(item,index) in dashboard.trend" :key="index" :title="`${item.date}: ${item.requests}`" :style="{height: `${Math.max(8, Math.min(100, item.requests * 9))}%`}" /></div></div></template></section>
      <section v-else-if="route.name === 'qa'" class="page split"><div class="panel"><p class="eyebrow">ASK A QUESTION</p><textarea v-model="question" rows="7" /><button class="primary" @click="ask">发送问题</button></div><div class="panel answer"><p class="eyebrow">ANSWER</p><p v-if="loading">正在生成回答…</p><p v-else-if="error" class="error">{{ error }}</p><p v-else>{{ answer || '回答会显示在这里。' }}</p></div></section>
      <section v-else-if="route.name === 'billing'" class="page"><div class="panel"><h3>账单与计量明细</h3><p>导出包含 request_id、Key、模型、Token 和缓存命中状态的可追溯账单。</p><button class="primary" @click="exportBill('csv')">导出 CSV</button><button @click="exportBill('xlsx')">导出 Excel</button></div></section>
      <section v-else class="page"><div class="panel"><p class="eyebrow">{{ title }}</p><h3>模块已就绪</h3><p>该页面提供 {{ title }} 的入口；接口可通过后端 /docs 完成新增、查询和管理操作。</p><button class="primary" @click="go('dashboard')">查看用量工作台</button></div></section>
    </main></div>
</template>
