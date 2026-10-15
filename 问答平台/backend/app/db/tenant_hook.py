# -*- coding: utf-8 -*-
"""多租户数据隔离的统一实现层(模块 B,FR-B2 核心)。

设计:
1. 所有业务表(KnowledgeBase / Document / 后续用量流水表)继承 app.db.base.TenantMixin;
2. 查询:ORM 事件 do_orm_execute 统一注入 `tenant_id = 当前租户` 条件,
   端点代码里不需要也不允许手写租户条件;
3. 写入:ORM 事件 before_flush 统一把关(fail-closed):
   - 无租户上下文时业务模型 → 拒绝;身份层登录/注册 → 放行;seed 显式 bypass;
   - 有上下文且对象没填 tenant_id → 自动补全;
   - 有上下文且 tenant_id 与上下文不一致 → 抛错拒绝(防跨租户写入);
4. 批量 ORM DML(update/delete/insert 语句)会绕过对象级守卫,直接禁止。

为什么上下文挂在 session.info 而不是 ContextVar:
FastAPI 的同步 def 端点运行在线程池,依赖函数与端点函数可能不在同一线程,
ContextVar 无法可靠传递;而同一请求内 FastAPI 依赖缓存复用同一个 Session 对象,
session.info 随 Session 走,最可靠。

事件注册时机:本模块被导入时即调用 register_tenant_hook() 完成事件注册
(注册在 Session 类上,对所有 Session 实例生效);该函数幂等,全项目可安全重复调用。
"""

from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.sql import visitors
from sqlalchemy.sql.schema import Table
from sqlalchemy.orm import Session, with_loader_criteria

from app.core.errors import BizError
from app.db.base import TenantMixin

# session.info 使用的键名(统一常量,防止拼写错误)
TENANT_ID_KEY = "tenant_id"          # 当前请求所属租户 ID(int)
TENANT_BYPASS_KEY = "tenant_bypass"  # True 时跳过隔离(仅平台管理员设置)


@contextmanager
def bypass_tenant_filter(session: Session):
    """只允许 require_super_admin 授权后使用；退出时恢复原状态。"""
    previous = session.info.get(TENANT_BYPASS_KEY)
    session.info[TENANT_BYPASS_KEY] = True
    try:
        yield session
    finally:
        if previous is None:
            session.info.pop(TENANT_BYPASS_KEY, None)
        else:
            session.info[TENANT_BYPASS_KEY] = previous


# ---------------------------------------------------------------
# 查询拦截:给所有 TenantMixin 子类自动注入租户条件
# ---------------------------------------------------------------
def _inject_tenant_criteria(execute_state):
    """每次 ORM 查询执行前触发。

    - 无上下文的业务表查询 → fail-closed 拒绝;身份层查询/显式授权维护任务放行;
    - SELECT:用 with_loader_criteria 把 `tenant_id = 当前租户` 追加进 WHERE
      (主查询、子查询、join 全部生效;mixin 基类会自动展开到每个映射子类);
    - 批量 ORM DML(update/delete/insert 语句)不经对象守卫,直接禁止,
      强制"先查询(已被过滤)再修改对象"。
    """
    tid = execute_state.session.info.get(TENANT_ID_KEY)
    if execute_state.session.info.get(TENANT_BYPASS_KEY):
        return
    # 无上下文只允许身份层的引导流程(登录/注册/seed),不允许业务表无过滤查询。
    # 业务请求必须先经 get_current_user 写入 tenant_id,否则在数据层 fail-closed。
    if tid is None:
        if execute_state.is_select and execute_state.is_orm_statement:
            # ORM mapper/Column 跨多种查询形态;逐项检查最终语句引用的表。
            business_tables = {m.__table__ for m in TenantMixin.__subclasses__() if hasattr(m, "__table__")}
            if any(isinstance(item, Table) and item in business_tables for item in visitors.iterate(execute_state.statement)):
                raise BizError("TENANT_CONTEXT_REQUIRED", "租户上下文缺失", 500)
        elif execute_state.is_orm_statement and (execute_state.is_update or execute_state.is_delete or execute_state.is_insert):
            table = getattr(execute_state.statement, "table", None)
            if table is not None and table in {m.__table__ for m in TenantMixin.__subclasses__() if hasattr(m, "__table__")}:
                raise BizError("TENANT_CONTEXT_REQUIRED", "租户上下文缺失", 500)
        return

    if execute_state.is_select:
        # 列刷新/关系加载不需要重复注入:租户条件已随对象传播(propagate_to_loaders)
        if execute_state.is_column_load or execute_state.is_relationship_load:
            return
        if not execute_state.is_orm_statement:
            return  # 纯 Core 语句不含 ORM 实体,无需处理
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                TenantMixin,
                lambda cls: cls.tenant_id == tid,  # cls 会被展开成每个映射子类
                include_aliases=True,
            )
        )
    elif (
        execute_state.is_orm_statement
        and (
            execute_state.is_update
            or execute_state.is_delete
            or execute_state.is_insert
        )
    ):
        raise BizError(
            "TENANT_ISOLATION_VIOLATION",
            "租户上下文中禁止批量 DML 语句,请改为先查询后修改对象的方式",
            500,
        )


# ---------------------------------------------------------------
# 写入拦截:flush 前检查本次要写/删的对象
# ---------------------------------------------------------------
def _guard_tenant_writes(session, flush_context, instances):
    """flush 前触发(session.new/dirty/deleted 均为 IdentitySet,支持 union)。"""
    tid = session.info.get(TENANT_ID_KEY)
    if session.info.get(TENANT_BYPASS_KEY):
        return  # 显式授权的后台脚本或超管请求
    if tid is None and any(isinstance(obj, TenantMixin) for obj in session.new.union(session.dirty).union(session.deleted)):
        raise BizError("TENANT_CONTEXT_REQUIRED", "租户上下文缺失", 500)
    if tid is None:
        return  # 身份层对象不受租户业务表钩子约束

    for obj in session.new.union(session.dirty).union(session.deleted):
        if not isinstance(obj, TenantMixin):
            continue  # User/Role/Tenant 属身份层,不在此守卫范围内
        obj_tid = obj.tenant_id
        if obj_tid is None:
            obj.tenant_id = tid  # 自动补全:端点漏填也能落到正确租户
        elif obj_tid != tid:
            # fail-closed:任何跨租户写入一律拒绝
            raise BizError(
                "TENANT_ISOLATION_VIOLATION",
                "检测到跨租户数据写入,操作已被阻止",
                403,
            )


# ---------------------------------------------------------------
# 事件注册:幂等,重复调用/重复导入只生效一次
# ---------------------------------------------------------------
_HOOK_REGISTERED = False


def register_tenant_hook() -> None:
    """把租户隔离事件注册到 Session 上(幂等:多次调用只注册一次)。"""
    global _HOOK_REGISTERED
    if _HOOK_REGISTERED:
        return
    event.listen(Session, "do_orm_execute", _inject_tenant_criteria)
    event.listen(Session, "before_flush", _guard_tenant_writes)
    _HOOK_REGISTERED = True


# 导入即注册,保留原有行为(注册在 Session 类上,对所有 Session 实例生效)
register_tenant_hook()
