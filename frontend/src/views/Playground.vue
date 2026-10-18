<!-- H5 问答体验页:展示答案与引用来源,并给出 Token/缓存/延迟等计量信息(FR-H5)。 -->
<script setup>
import { reactive, ref } from 'vue'
import { api, friendlyMessage } from '../api'
import StateWrapper from '../components/StateWrapper.vue'

const form = reactive({ question: '牛顿第二定律是什么?', apiKey: '' })
const loading = ref(false)
const error = ref('')
const answer = ref(null)

async function ask() {
  error.value = ''
  if (!form.question.trim()) {
    error.value = '请输入要提问的内容'
    return
  }
  loading.value = true
  try {
    answer.value = await api.ask(
      { question: form.question.trim() },
      form.apiKey.trim() || undefined,
    )
  } catch (err) {
    // 限流/配额超限的提示要带上可操作的恢复信息(何时重试、何时重置)
    const extra = err.data || {}
    const suffix = err.code === 'RATE_LIMITED' && extra.retry_after
      ? `（建议 ${extra.retry_after} 秒后重试）`
      : err.code === 'QUOTA_EXCEEDED' && extra.reset_at
        ? `（配额将于 ${extra.reset_at} 重置）`
        : ''
    error.value = friendlyMessage(err) + suffix
    answer.value = null
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <section class="page split">
    <div class="panel">
      <p class="eyebrow">ASK A QUESTION</p>
      <textarea v-model="form.question" rows="7" aria-label="问题内容" />
      <label class="inline-label">
        使用 API Key 调用<span class="optional">(可选,留空则用登录态)</span>
        <input v-model="form.apiKey" placeholder="sk-…" />
      </label>
      <button class="primary" :disabled="loading" @click="ask">
        {{ loading ? '正在生成回答…' : '发送问题' }}
      </button>
      <p class="hint">提问会先做配额预检与限流,再查缓存,最后才调用模型。</p>
    </div>

    <div class="panel answer">
      <p class="eyebrow">ANSWER</p>
      <StateWrapper :loading="loading" :error="error" :is-empty="!answer"
                    empty-text="回答会显示在这里" loading-text="正在生成回答…" @retry="ask">
        <p v-if="answer.cache_hit" class="badge badge-ok">来自缓存(未调用模型,Token 计 0)</p>
        <p class="answer-text">{{ answer.answer }}</p>

        <div class="sources" v-if="answer.sources && answer.sources.length">
          <p class="eyebrow">引用来源</p>
          <ul>
            <!-- 引用来源按序号渲染,key 用下标 + 内容前缀保证稳定 -->
            <li v-for="(source, index) in answer.sources" :key="`${index}-${source.slice(0, 12)}`">
              {{ source }}
            </li>
          </ul>
        </div>

        <table class="meta-table">
          <tbody>
            <tr><th>模型</th><td>{{ answer.model }}</td></tr>
            <tr><th>输入 Token</th><td>{{ answer.tokens.prompt_tokens }}</td></tr>
            <tr><th>输出 Token</th><td>{{ answer.tokens.completion_tokens }}</td></tr>
            <tr><th>合计 Token</th><td>{{ answer.tokens.total_tokens }}</td></tr>
            <tr><th>延迟</th><td>{{ answer.latency_ms }} ms</td></tr>
          </tbody>
        </table>
      </StateWrapper>
    </div>
  </section>
</template>
