<template><div ref="el" class="chart"></div></template>
<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
const props = defineProps({ trend: { type: Array, default: () => [] } })
const el = ref(); let chart
function render() { if (!el.value) return; chart ||= echarts.init(el.value); chart.setOption({ tooltip: { trigger: 'axis' }, legend: { textStyle: { color: '#dbe8f4' } }, xAxis: { type: 'category', data: props.trend.map(x => x.date), axisLabel: { color: '#b8c7d9' } }, yAxis: [{ type: 'value', name: '请求', axisLabel: { color: '#b8c7d9' } }, { type: 'value', name: 'Token', axisLabel: { color: '#b8c7d9' } }], series: [{ name: '请求', type: 'line', smooth: true, data: props.trend.map(x => x.requests) }, { name: '输入 Token', type: 'line', yAxisIndex: 1, data: props.trend.map(x => x.prompt_tokens) }, { name: '输出 Token', type: 'line', yAxisIndex: 1, data: props.trend.map(x => x.completion_tokens) }] }) }
onMounted(render); watch(() => props.trend, render, { deep: true }); onBeforeUnmount(() => chart?.dispose())
</script>
