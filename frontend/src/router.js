import { createRouter, createWebHashHistory } from 'vue-router'

const pages = ['login', 'tenant', 'dashboard', 'keys', 'qa', 'billing']
const routes = pages.map((name) => ({
  path: name === 'login' ? '/login' : `/${name}`,
  name,
  component: () => import('./App.vue'),
  meta: { requiresAuth: name !== 'login' },
}))

const router = createRouter({ history: createWebHashHistory(), routes })
router.beforeEach((to) => {
  if (to.meta.requiresAuth && !localStorage.getItem('smartqa_token')) return { name: 'login' }
  if (to.name === 'login' && localStorage.getItem('smartqa_token')) return { name: 'dashboard' }
})
export default router
