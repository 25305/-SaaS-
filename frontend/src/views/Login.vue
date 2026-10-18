<!-- H1 登录页:表单校验 + 错误提示 + 登录态保持(FR-H1 / FR-A6)。 -->
<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, friendlyMessage, getToken, saveSession } from '../api'

const router = useRouter()
const route = useRoute()

const form = reactive({ username: '', password: '', tenant_code: '' })
const errors = reactive({ username: '', password: '' })
const loading = ref(false)
const message = ref('')
// 登录态保持:已有 token 时直接回到登录前想去的页面(而不是每次都重新输入)
const alreadyLoggedIn = ref(Boolean(getToken()))

const demoAccounts = [
  { username: 'platform_admin', password: 'Platform@123', note: '平台管理员(全局租户管理)' },
  { username: 'academic_admin', password: 'Academic@123', note: '学术问答 · 租户管理员' },
  { username: 'academic_viewer', password: 'Viewer@123', note: '学术问答 · 只读成员' },
  { username: 'physics_admin', password: 'Physics@123', note: '物理问答 · 租户管理员' },
]

function validate() {
  errors.username = form.username.trim() ? '' : '请输入用户名'
  errors.password = form.password ? '' : '请输入密码'
  if (form.password && form.password.length < 6) errors.password = '密码至少 6 位'
  return !errors.username && !errors.password
}

function fill(account) {
  form.username = account.username
  form.password = account.password
  errors.username = ''
  errors.password = ''
}

async function submit() {
  message.value = ''
  if (!validate()) return
  loading.value = true
  try {
    const payload = {
      username: form.username.trim(),
      password: form.password,
      ...(form.tenant_code.trim() ? { tenant_code: form.tenant_code.trim() } : {}),
    }
    const token = await api.login(payload)
    saveSession({ token: token.access_token })
    // 登录后立刻取角色/租户:路由守卫与页面展示都依赖它(FR-A6)
    const me = await api.me()
    saveSession({ role: me.role, tenant: me.tenant_code })
    router.push(route.query.redirect || { name: 'workbench' })
  } catch (err) {
    message.value = friendlyMessage(err)
  } finally {
    loading.value = false
  }
}

function continueSession() {
  router.push(route.query.redirect || { name: 'workbench' })
}
</script>

<template>
  <main class="login-page">
    <section class="login-card">
      <p class="eyebrow">SMARTQA / TENANT OPS</p>
      <h1>进入智能问答<br><em>SaaS 管理台</em></h1>
      <p class="login-hint">使用分配给您的租户账号登录。用户名全局唯一,租户编码可选。</p>

      <template v-if="alreadyLoggedIn">
        <p class="state">检测到本机已有登录态。</p>
        <button class="primary" @click="continueSession">继续使用当前登录</button>
        <button @click="alreadyLoggedIn = false">换一个账号登录</button>
      </template>

      <form v-else novalidate @submit.prevent="submit">
        <label>
          用户名
          <input v-model="form.username" autocomplete="username" placeholder="academic_admin" />
        </label>
        <p v-if="errors.username" class="error">{{ errors.username }}</p>

        <label>
          密码
          <input v-model="form.password" type="password" autocomplete="current-password"
                 placeholder="••••••••" />
        </label>
        <p v-if="errors.password" class="error">{{ errors.password }}</p>

        <label>
          租户编码<span class="optional">(可选)</span>
          <input v-model="form.tenant_code" placeholder="academic" />
        </label>

        <button class="primary" type="submit" :disabled="loading">
          {{ loading ? '登录中…' : '进入工作台 →' }}
        </button>
      </form>

      <p v-if="message" class="error" role="alert">{{ message }}</p>

      <div class="demo-accounts">
        <p class="eyebrow">演示账号(点击填充)</p>
        <button v-for="account in demoAccounts" :key="account.username" type="button"
                class="demo-account" @click="fill(account)">
          <code>{{ account.username }}</code>
          <span>{{ account.note }}</span>
        </button>
      </div>
    </section>
  </main>
</template>
