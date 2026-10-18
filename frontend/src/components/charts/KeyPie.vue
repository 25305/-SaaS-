<!-- FR-F3 Key 消耗饼图:按 Key 分组的 Token 占比(只展示掩码,不泄露完整 Key)。 -->
<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  byKey: { type: Array, default: () => [] },
})

const el = ref(null)
let chart = null

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  chart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}<br/>Token: {c} ({d}%)' },
    legend: { bottom: 0, textStyle: { color: '#cbd7e4' } },
    series: [
      {
        name: 'Key 消耗',
        type: 'pie',
        radius: ['42%', '68%'],
        center: ['50%', '44%'],
        itemStyle: { borderColor: '#101a2b', borderWidth: 2 },
        label: { color: '#e6edf3' },
        data: props.byKey.map((row) => ({
          // 只显示 Key 前缀/jwt 分组,绝不显示完整 Key
          name: row.key_prefix,
          value: row.total_tokens,
        })),
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

watch(() => props.byKey, render, { deep: true })
</script>

<template>
  <div ref="el" class="chart-canvas" />
</template>
