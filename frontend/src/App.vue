<template>
  <RouterView v-if="route.name === 'login'" />
  <div v-else class="shell"><aside><div class="brand"><b>Q</b><span>SMART<br>QA</span></div><nav><RouterLink v-if="isSuper" to="/admin">租户管理</RouterLink><RouterLink to="/workbench">工作台</RouterLink><RouterLink v-if="isAdmin" to="/keys">Key 管理</RouterLink><RouterLink to="/playground">问答体验</RouterLink><RouterLink to="/billing">账单与导出</RouterLink></nav><button class="logout" @click="logout">退出登录</button></aside><main class="workspace"><header><div><p class="eyebrow">{{ user?.tenant_code || 'TENANT' }}</p><h2>{{ title }}</h2></div><span class="user">{{ user?.username || '加载中…' }}</span></header><RouterView /></main></div>
</template>
<script setup>
import { computed, onMounted, ref } from 'vue'; import { useRoute, useRouter } from 'vue-router'; import { request } from './api'
const route=useRoute(),router=useRouter(),user=ref(null)
const title=computed(()=>({ 'tenant-admin':'租户管理台', workbench:'用量工作台', 'api-keys':'API Key 管理', playground:'问答体验', billing:'账单与导出' }[route.name] || '智能问答 SaaS'))
const isSuper=computed(()=>user.value?.role==='super_admin'), isAdmin=computed(()=>['admin','super_admin'].includes(user.value?.role))
async function loadMe(){try{user.value=await request('/api/auth/me');localStorage.setItem('smartqa_role',user.value.role)}catch{logout()}}
function logout(){localStorage.removeItem('smartqa_token');localStorage.removeItem('smartqa_role');router.push({name:'login'})}
onMounted(loadMe)
</script>
