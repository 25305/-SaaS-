<template>
  <p v-if="loading" class="state">正在加载…</p>
  <section v-else-if="error" class="state error"><p>{{ error }}</p><button @click="$emit('retry')">重试</button></section>
  <p v-else-if="empty" class="state">{{ emptyText }}</p>
  <slot v-else />
</template>
<script setup>
defineProps({ loading: Boolean, error: { type: String, default: '' }, empty: Boolean, emptyText: { type: String, default: '暂无数据' } })
defineEmits(['retry'])
</script>
