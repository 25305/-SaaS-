<template>
  <RouterView v-if="route.name === 'login'" />
  <div v-else class="shell"><aside><div class="brand"><b>Q</b><span>SMART<br>QA</span></div><nav><RouterLink v-if="isSuper" to="/admin">租户管理</RouterLink><RouterLink to="/workbench">工作台</RouterLink><RouterLink v-if="isAdmin" to="/keys">Key 管理</RouterLink><RouterLink to="/playground">问答体验</RouterLink><RouterLink to="/billing">账单与导出</RouterLink></nav><button class="logout" @click="logout">退出登录</button></aside><main class="workspace"><header><div><p class="eyebrow">{{ user?.tenant_code || 'TENANT' }}</p><h2>{{ title }}</h2></div><div class="identity"><strong>{{ user?.username || '加载中…' }}</strong><span class="role-badge">{{ currentRoleLabel }}</span></div></header><p v-if="forbidden" class="access-notice">当前账号没有该页面的访问权限，已返回可用页面。</p><p v-if="isReadonly" class="readonly-notice"><b>只读模式</b> 当前账号只能查看数据，不能执行管理操作。</p><RouterView /></main></div>
</template>
<script setup>
import { computed, onMounted, ref, watch } from 'vue'; import { useRoute, useRouter } from 'vue-router'; import { request } from './api'; import { roleLabel } from './auth'
const route=useRoute(),router=useRouter(),user=ref(null),forbidden=ref(false)
const title=computed(()=>({ 'tenant-admin':'租户管理台', workbench:'用量工作台', 'api-keys':'API Key 管理', playground:'问答体验', billing:'账单与导出' }[route.name] || '智能问答 SaaS'))
const isSuper=computed(()=>user.value?.role==='super_admin'),isAdmin=computed(()=>user.value?.role==='admin'),isReadonly=computed(()=>user.value?.role==='readonly')
const currentRoleLabel=computed(()=>roleLabel[user.value?.role] || '身份加载中')
async function loadMe(){try{user.value=await request('/api/auth/me');localStorage.setItem('smartqa_role',user.value.role);localStorage.setItem('smartqa_username',user.value.username);localStorage.setItem('smartqa_tenant',user.value.tenant_code)}catch{logout()}}
function logout(){user.value=null;forbidden.value=false;for(const key of ['smartqa_token','smartqa_role','smartqa_username','smartqa_tenant']) localStorage.removeItem(key);router.push({name:'login'})}
watch(()=>route.query.reason,(reason)=>{if(reason==='forbidden'){forbidden.value=true;const query={...route.query};delete query.reason;router.replace({query})}else if(!reason&&route.name==='login'){forbidden.value=false}},{immediate:true})
watch(()=>route.name,(name,previous)=>{if(name!=='login'&&previous==='login') loadMe()})
onMounted(loadMe)
</script>
