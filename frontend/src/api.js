export const token = () => localStorage.getItem('smartqa_token') || ''
export const headers = () => ({ Authorization: `Bearer ${token()}`, 'Content-Type': 'application/json' })

export async function request(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...headers(), ...(options.headers || {}) } })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(body.message || `请求失败 (${response.status})`)
  return body.data
}
