/**
 * 统一 API 封装(前端所有页面共用)。
 *
 * 约定(与后端、路由守卫一致):
 * - 登录态存 localStorage:`smartqa_token` / `smartqa_role` / `smartqa_tenant`;
 * - 请求头带 `Authorization: Bearer <token>`,`X-API-Key` 仅问答体验页可选;
 * - 后端统一响应体 `{code, message, data}`,`request()` 直接返回 `data` 部分;
 * - 401 一律清除本地登录态并跳登录页,避免"看起来还登录着但每个请求都失败"。
 *
 * 为什么不用 axios:项目未引入该依赖,浏览器原生 fetch 已足够,避免新增包。
 */

export const TOKEN_KEY = 'smartqa_token'
export const ROLE_KEY = 'smartqa_role'
export const TENANT_KEY = 'smartqa_tenant'

export class ApiError extends Error {
  constructor(code, message, data, status) {
    super(message)
    this.code = code
    this.data = data
    this.status = status
  }
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function getRole() {
  return localStorage.getItem(ROLE_KEY) || ''
}

export function getTenant() {
  return localStorage.getItem(TENANT_KEY) || ''
}

export function saveSession({ token, role, tenant }) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  if (role) localStorage.setItem(ROLE_KEY, role)
  if (tenant) localStorage.setItem(TENANT_KEY, tenant)
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(ROLE_KEY)
  localStorage.removeItem(TENANT_KEY)
}

/** 把后端错误码翻成前端展示文案(与《接口文档》错误码表对齐)。 */
export function friendlyMessage(err) {
  const table = {
    QUOTA_EXCEEDED: '本周期用量已超出配额,请等待配额重置或联系管理员',
    RATE_LIMITED: '请求过于频繁,请稍后重试',
    INVALID_TOKEN: '登录已过期,请重新登录',
    UNAUTHORIZED: '未登录或认证信息缺失',
    FORBIDDEN: '当前角色无权执行该操作',
    API_KEY_INVALID: 'API Key 无效或已失效',
    MODEL_SERVICE_ERROR: '模型服务暂时不可用,请稍后重试',
    VALIDATION_ERROR: '参数校验失败,请检查输入',
  }
  return table[err?.code] || err?.message || '请求失败'
}

export async function request(path, options = {}) {
  const { method = 'GET', body, headers = {}, raw = false, apiKey } = options
  const token = getToken()
  const response = await fetch(path, {
    method,
    headers: {
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(apiKey ? { 'X-API-Key': apiKey } : {}),
      ...headers,
    },
    body: body ? JSON.stringify(body) : undefined,
  })

  if (raw) return response

  let payload = null
  try {
    payload = await response.json()
  } catch {
    payload = null
  }

  if (!response.ok) {
    // 401 = 登录态失效:清本地态,交给路由守卫跳登录页
    if (response.status === 401) clearSession()
    throw new ApiError(
      payload?.code || 'HTTP_ERROR',
      payload?.message || `请求失败(${response.status})`,
      payload?.data,
      response.status,
    )
  }
  return payload?.data ?? payload
}

/** 带鉴权头下载文件(账单导出)。window.open 无法带 Authorization 头,故用 fetch+blob。 */
export async function download(path, filename) {
  const response = await request(path, { raw: true })
  if (!response.ok) throw new ApiError('EXPORT_FAILED', '导出失败,请稍后重试', null, response.status)
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

// ---------------- 各模块接口(集中在此,页面不拼 URL) ----------------
export const api = {
  login: (payload) => request('/api/auth/login', { method: 'POST', body: payload }),
  me: () => request('/api/auth/me'),
  logout: () => request('/api/auth/logout', { method: 'POST' }),

  tenants: () => request('/api/admin/tenants'),
  createTenant: (payload) => request('/api/admin/tenants', { method: 'POST', body: payload }),
  setTenantActive: (id, active) =>
    request(`/api/admin/tenants/${id}/${active ? 'enable' : 'disable'}`, { method: 'POST' }),
  // 平台管理员:设置租户配额与超额策略(FR-D1/FR-D6)、查看全局用量(FR-A2/FR-F2)
  setTenantQuota: (id, payload) =>
    request(`/api/admin/tenants/${id}/quota`, { method: 'PATCH', body: payload }),
  globalUsage: (range = 'month') => request(`/api/admin/usage/overview?range=${range}`),

  dashboard: (range = 'month') => request(`/api/v1/dashboard?range=${range}`),
  quotaStatus: () => request('/api/v1/quota/status'),
  recentRecords: (limit = 20) => request(`/api/usage/records?limit=${limit}`),

  keys: () => request('/api/v1/apikeys'),
  createKey: (payload) => request('/api/v1/apikeys', { method: 'POST', body: payload }),
  rotateKey: (id) => request(`/api/v1/apikeys/${id}/rotate`, { method: 'POST' }),
  revokeKey: (id) => request(`/api/v1/apikeys/${id}`, { method: 'DELETE' }),

  ask: (payload, apiKey) =>
    request('/api/v1/qa/ask', { method: 'POST', body: payload, apiKey }),

  billingSummary: (period) => request(`/api/v1/billing/summary?period=${period}`),
  billingDaily: (period) => request(`/api/v1/billing/daily?period=${period}`),
  billingDetail: (period, limit = 100) =>
    request(`/api/v1/billing/detail?period=${period}&limit=${limit}`),
  billingVerify: (period) => request(`/api/v1/billing/verify?period=${period}`),
}
