import { createRouter, createWebHashHistory } from 'vue-router'
import Login from './views/Login.vue'
import TenantAdmin from './views/TenantAdmin.vue'
import Workbench from './views/Workbench.vue'
import ApiKeys from './views/ApiKeys.vue'
import Playground from './views/Playground.vue'
import Billing from './views/Billing.vue'

const routes = [
  { path: '/login', name: 'login', component: Login },
  { path: '/admin', name: 'tenant-admin', component: TenantAdmin, meta: { requiresAuth: true, role: 'super_admin' } },
  { path: '/workbench', name: 'workbench', component: Workbench, meta: { requiresAuth: true } },
  { path: '/keys', name: 'api-keys', component: ApiKeys, meta: { requiresAuth: true, role: 'admin' } },
  { path: '/playground', name: 'playground', component: Playground, meta: { requiresAuth: true } },
  { path: '/billing', name: 'billing', component: Billing, meta: { requiresAuth: true } },
  { path: '/', redirect: '/workbench' },
]
const router = createRouter({ history: createWebHashHistory(), routes })
router.beforeEach((to) => {
  const token = localStorage.getItem('smartqa_token')
  const role = localStorage.getItem('smartqa_role')
  if (to.meta.requiresAuth && !token) return { name: 'login', query: { redirect: to.fullPath } }
  // 角色尚未加载时同样拒绝受限页，避免刷新/直达 URL 时出现短暂放行。
  if (to.meta.role && role !== 'super_admin' && role !== to.meta.role) return { name: 'workbench' }
  if (to.name === 'login' && token) return { name: 'workbench' }
})
export default router
