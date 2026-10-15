# -*- coding: utf-8 -*-
"""多租户数据隔离的统一实现层(模块 B,FR-B2 核心)。

设计:
1. 所有业务表(KnowledgeBase / Document / 后续用量流水表)继承 models.TenantMixin;
2. 查询:ORM 事件 do_orm_execute 统一注入 `tenant_id = 当前租户` 条件,
   端点代码里不需要也不允许手写租户条件;
3. 写入:ORM 事件 before_flush 统一把关(fail-closed):
   - 无租户上下文(登录/注册/种子数据)→ 放行;
   - 有上下文且对象没填 tenant_id → 自动补全;
   - 有上下文且 tenant_id 与上下文不一致 → 抛错拒绝(防跨租户写入);
4. 批量 ORM DML(update/delete/insert 语句)会绕过对象级守卫,直接禁止。

为什么上下文挂在 session.info 而不是 ContextVar:
FastAPI 的同步 def 端点运行在线程池,依赖函数与端点函数可能不在同一线程,
ContextVar 无法可靠传递;而同一请求内 FastAPI 依赖缓存复用同一个 Session 对象,
session.info 随 Session 走,最可靠。

事件注册时机:本模块在 app/main.py 中于 `from app import models` 之后被导入,
导入即完成事件注册(注册在 Session 类上,对所有 Session 实例生效)。
"""

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from app.core.errors import BizError
from app.models import TenantMixin

# session.info 使用的键名(统一常量,防止拼写错误)
TENANT_ID_KEY = "tenant_id"          # 当前请求所属租户 ID(int)
TENANT_BYPASS_KEY = "tenant_bypass"  # True 时跳过隔离(仅平台管理员设置)


# ---------------------------------------------------------------
# 查询拦截:给所有 TenantMixin 子类自动注入租户条件
# ---------------------------------------------------------------
@event.listens_for(Session, "do_orm_execute")
def _inject_tenant_criteria(execute_state):
    """每次 ORM 查询执行前触发。

    - 无租户上下文(登录/注册/种子)或平台管理员声明绕过 → 不处理;
    - SELECT:用 with_loader_criteria 把 `tenant_id = 当前租户` 追加进 WHERE
      (主查询、子查询、join 全部生效;mixin 基类会自动展开到每个映射子类);
    - 批量 ORM DML(update/delete/insert 语句)不经对象守卫,直接禁止,
      强制"先查询(已被过滤)再修改对象"。
    """
    tid = execute_state.session.info.get(TENANT_ID_KEY)
    if tid is None or execute_state.session.info.get(TENANT_BYPASS_KEY):
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
@event.listens_for(Session, "before_flush")
def _guard_tenant_writes(session, flush_context, instances):
    """flush 前触发(session.new/dirty/deleted 均为 IdentitySet,支持 union)。"""
    tid = session.info.get(TENANT_ID_KEY)
    if tid is None or session.info.get(TENANT_BYPASS_KEY):
        return  # 无上下文(登录/注册/种子)或超管绕过 → 放行

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
