<!--
  三态封装组件(FR-H7:所有页面"三态齐备")。

  为什么抽成组件:每页各写一遍 loading / error / empty 判断,最常见的偏差是
  **漏写 empty 态**(把"没有数据"渲染成空白或 0,用户以为系统坏了)。
  抽成组件后,三态由结构统一保证,页面只需要传 props。

  用法:
    <StateWrapper :loading="loading" :error="error" :is-empty="!rows.length"
                  empty-text="暂无数据" @retry="load"> ...内容... </StateWrapper>
-->
<script setup>
defineProps({
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  isEmpty: { type: Boolean, default: false },
  emptyText: { type: String, default: '暂无数据' },
  loadingText: { type: String, default: '正在加载…' },
})
defineEmits(['retry'])
</script>

<template>
  <!-- 一态:加载中 -->
  <div v-if="loading" class="state state-loading">
    <span class="spinner" aria-hidden="true" />
    <p>{{ loadingText }}</p>
  </div>

  <!-- 二态:错误(可重试) -->
  <div v-else-if="error" class="state state-error" role="alert">
    <p>{{ error }}</p>
    <button class="primary" @click="$emit('retry')">重试</button>
  </div>

  <!-- 三态:空数据 -->
  <div v-else-if="isEmpty" class="state state-empty">
    <p>{{ emptyText }}</p>
    <button class="primary" @click="$emit('retry')">刷新</button>
  </div>

  <!-- 正常态:交给页面自己的内容 -->
  <slot v-else />
</template>
