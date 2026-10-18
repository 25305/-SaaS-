<!--
  应用外壳(模块 H):登录页独立渲染,其余页面统一"侧边导航 + 顶栏 + 内容区"。

  导航项按角色过滤:viewer 看不到租户管理与 Key 管理入口(即便手输 URL,
  路由守卫 meta.roles 也会把他推回工作台)。
-->
<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, clearSession, getRole, getTenant } from './api'

const route = useRoute()
const router = useRouter()

// 登录页不套外壳,避免"未登录却看到导航"的错觉
const isLogin = computed(() => route.name === 'login')

// localStorage 不是响应式数据源:如果直接写 computed(() => getRole()),
// 该 computed 没有任何响应式依赖,首次求值后会被永久缓存 —— App.vue 作为外壳
// 不随路由重新挂载,于是登录/登出/换账号后顶栏角色与导航都会停留在旧值,
// 必须整页刷新才恢复。故用 ref + 路由变化时重新读取。
const role = ref(getRole())
const tenant = ref(getTenant())
watch(
  () => route.fullPath,
  () => {
    role.value = getRole()
    tenant.value = getTenant()
  },
)

const NAV = [
  { name: 'workbench', label: '用量工作台' },
  { name: 'tenantAdmin', label: '租户管理', roles: ['super_admin'] },
  { name: 'apiKeys', label: 'API Key 管理', roles: ['admin', 'super_admin'] },
  { name: 'playground', label: '问答体验' },
  { name: 'billing', label: '账单导出' },
]

const visibleNav = computed(() =>
  NAV.filter((item) => !item.roles || item.roles.includes(role.value)))

const TITLES = {
  workbench: '用量工作台',
  tenantAdmin: '租户管理台',
  apiKeys: 'API Key 管理',
  playground: '问答体验',
  billing: '账单与计量',
}
const title = computed(() => TITLES[route.name] || '智能问答 SaaS')

async function logout() {
  try {
    await api.logout()
  } catch {
    // 退出接口失败(如 Token 已过期)不影响本地清理,务必仍然登出
  }
  clearSession()
  router.push({ name: 'login' })
}
</script>

<template>
  <router-view v-if="isLogin" />

  <div v-else class="shell">
    <aside>
      <div class="brand"><b>Q</b><span>SMART<br />QA</span></div>
      <nav>
        <!-- FR-H7:v-for 使用路由名做业务唯一 key,不用 index -->
        <button v-for="item in visibleNav" :key="item.name"
                :class="{ active: route.name === item.name }"
                @click="router.push({ name: item.name })">
          {{ item.label }}
        </button>
      </nav>
      <button class="logout" @click="logout">退出登录</button>
    </aside>

    <main class="workspace">
      <header>
        <div>
          <p class="eyebrow">TENANT / {{ (tenant || 'unknown').toUpperCase() }}</p>
          <h2>{{ title }}</h2>
        </div>
        <span class="badge badge-ok">{{ role || 'member' }}</span>
      </header>
      <router-view />
    </main>
  </div>
</template>
