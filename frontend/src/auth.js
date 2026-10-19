export const roleLabel = {
  super_admin: '平台管理员',
  admin: '租户管理员',
  readonly: '只读成员',
}

export function defaultRouteForRole(role) {
  return role === 'super_admin' ? '/admin' : '/workbench'
}

export function canAccessRoute(role, requiredRole) {
  if (!requiredRole) return true
  return role === requiredRole
}
