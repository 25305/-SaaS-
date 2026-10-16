<template><div ref="el" class="chart"></div></template>
<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
const props = defineProps({ rows: { type: Array, default: () => [] } }); const el = ref(); let chart
function render() { if (!el.value) return; chart ||= echarts.init(el.value); chart.setOption({ tooltip: {}, xAxis: { type: 'category', data: props.rows.map(x => x.bucket), axisLabel: { color: '#b8c7d9' } }, yAxis: { type: 'value', axisLabel: { color: '#b8c7d9' } }, series: [{ type: 'bar', data: props.rows.map(x => x.count), itemStyle: { color: '#d9ae59' } }] }) }
onMounted(render); watch(() => props.rows, render, { deep: true }); onBeforeUnmount(() => chart?.dispose())
</script>
