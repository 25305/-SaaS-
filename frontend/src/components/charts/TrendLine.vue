<!-- FR-F3 趋势折线图:请求数 + 输入/输出 Token(双 Y 轴)。 -->
<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  trend: { type: Array, default: () => [] },
})

const el = ref(null)
let chart = null

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['请求数', '输入 Token', '输出 Token'], textStyle: { color: '#cbd7e4' } },
    grid: { left: 12, right: 12, bottom: 8, top: 44, containLabel: true },
    xAxis: {
      type: 'category',
      data: props.trend.map((d) => d.date),
      axisLine: { lineStyle: { color: '#46617b' } },
      axisLabel: { color: '#9db0c4' },
    },
    yAxis: [
      { type: 'value', name: '请求数', nameTextStyle: { color: '#9db0c4' },
        axisLabel: { color: '#9db0c4' }, splitLine: { lineStyle: { color: '#22384f' } } },
      { type: 'value', name: 'Token', nameTextStyle: { color: '#9db0c4' },
        axisLabel: { color: '#9db0c4' }, splitLine: { show: false } },
    ],
    series: [
      { name: '请求数', type: 'line', smooth: true, data: props.trend.map((d) => d.requests) },
      { name: '输入 Token', type: 'line', smooth: true, yAxisIndex: 1,
        data: props.trend.map((d) => d.prompt_tokens) },
      { name: '输出 Token', type: 'line', smooth: true, yAxisIndex: 1,
        data: props.trend.map((d) => d.completion_tokens) },
    ],
  })
  chart.resize()
}

function resize() {
  if (chart) chart.resize()
}

onMounted(() => {
  render()
  window.addEventListener('resize', resize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  // 组件卸载必须 dispose,否则 ECharts 实例会泄漏并继续持有 DOM 引用
  if (chart) chart.dispose()
  chart = null
})

watch(() => props.trend, render, { deep: true })
</script>

<template>
  <div ref="el" class="chart-canvas" />
</template>
