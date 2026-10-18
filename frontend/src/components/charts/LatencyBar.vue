<!-- FR-F3 延迟分布柱状图:按耗时区间统计请求条数。 -->
<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  distribution: { type: Array, default: () => [] },
})

const el = ref(null)
let chart = null

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  chart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 12, right: 16, bottom: 8, top: 24, containLabel: true },
    xAxis: {
      type: 'category',
      data: props.distribution.map((d) => d.bucket),
      axisLine: { lineStyle: { color: '#46617b' } },
      axisLabel: { color: '#9db0c4' },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { color: '#9db0c4' },
      splitLine: { lineStyle: { color: '#22384f' } },
    },
    series: [
      {
        name: '请求数',
        type: 'bar',
        barMaxWidth: 48,
        itemStyle: { color: '#e0b85e' },
        data: props.distribution.map((d) => d.count),
      },
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
  if (chart) chart.dispose()
  chart = null
})

watch(() => props.distribution, render, { deep: true })
</script>

<template>
  <div ref="el" class="chart-canvas" />
</template>
