import { readFileSync, existsSync } from 'node:fs'
const root = new URL('../src/', import.meta.url)
const required = ['views/Login.vue', 'views/TenantAdmin.vue', 'views/Workbench.vue', 'views/ApiKeys.vue', 'views/Playground.vue', 'views/Billing.vue', 'components/StateWrapper.vue', 'components/charts/TrendLine.vue', 'components/charts/KeyPie.vue', 'components/charts/LatencyBar.vue']
for (const path of required) if (!existsSync(new URL(path, root))) throw new Error(`缺少 D 模块前端文件: ${path}`)
const router = readFileSync(new URL('router.js', root), 'utf8')
for (const path of ['/login', '/admin', '/workbench', '/keys', '/playground', '/billing']) if (!router.includes(`'${path}'`)) throw new Error(`缺少路由: ${path}`)
const playground = readFileSync(new URL('views/Playground.vue', root), 'utf8')
if (!playground.includes('sources')) throw new Error('问答页没有引用来源展示')
console.log('D 模块前端静态验收通过')
