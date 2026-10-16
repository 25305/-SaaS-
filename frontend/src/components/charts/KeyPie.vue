<template><div ref="el" class="chart"></div></template>
<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
const props = defineProps({ rows: { type: Array, default: () => [] } }); const el = ref(); let chart
function render() { if (!el.value) return; chart ||= echarts.init(el.value); chart.setOption({ tooltip: { trigger: 'item' }, series: [{ type: 'pie', radius: ['40%', '70%'], label: { color: '#dbe8f4' }, data: props.rows.map(x => ({ name: x.name || x.key_prefix, value: x.total_tokens })) }] }) }
onMounted(render); watch(() => props.rows, render, { deep: true }); onBeforeUnmount(() => chart?.dispose())
</script>
