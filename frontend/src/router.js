/**
 * 路由表(模块 H / FR-H1)。
 *
 * 约定:
 * - hash 模式(createWebHashHistory):静态部署无需服务端 rewrite;
 * - 路由名固定为 login / tenantAdmin / workbench / apiKeys / playground / billing;
 * - meta.requiresAuth:除 login 外都需登录;
 * - meta.roles:可选的角色白名单(tenantAdmin 仅 super_admin;apiKeys 需 admin/super_admin);
 * - 守卫读 localStorage 的 smartqa_token / smartqa_role,未登录跳登录并带 redirect。
 */
import { createRouter, createWebHashHistory } from 'vue-router'
import { TOKEN_KEY, ROLE_KEY } from './api'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('./views/Login.vue'),
  },
  {
    path: '/tenant-admin',
    name: 'tenantAdmin',
    component: () => import('./views/TenantAdmin.vue'),
    meta: { requiresAuth: true, roles: ['super_admin'] },
  },
  {
    path: '/workbench',
    name: 'workbench',
    component: () => import('./views/Workbench.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/api-keys',
    name: 'apiKeys',
    component: () => import('./views/ApiKeys.vue'),
    meta: { requiresAuth: true, roles: ['admin', 'super_admin'] },
  },
  {
    path: '/playground',
    name: 'playground',
    component: () => import('./views/Playground.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/billing',
    name: 'billing',
    component: () => import('./views/Billing.vue'),
    meta: { requiresAuth: true },
  },
  // 根路径与未匹配路径都归到工作台(有登录态则可见,否则被守卫拦到登录页)
  { path: '/', redirect: { name: 'workbench' } },
  { path: '/:pathMatch(.*)*', redirect: { name: 'workbench' } },
]

const router = createRouter({ history: createWebHashHistory(), routes })

router.beforeEach((to) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (to.meta.requiresAuth && !token) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && token) {
    return { name: 'workbench' }
  }
  // 角色白名单:不满足时回落工作台,避免直接闪现 403
  const roles = to.meta.roles
  if (roles && roles.length) {
    const role = localStorage.getItem(ROLE_KEY) || ''
    if (!roles.includes(role)) return { name: 'workbench' }
  }
  return true
})

export default router
