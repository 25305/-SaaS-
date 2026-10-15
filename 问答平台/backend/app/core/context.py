# -*- coding: utf-8 -*-
"""请求级上下文(模块 A 的接口契约,模块 C 依赖)。

`RequestContext` 是鉴权后的统一出口:Bearer(JWT)与 `X-API-Key` 两条路径都归一到
同一个对象,后续业务代码(配额预检、计量写入)只认它,不再关心调用方用的是哪种凭据。

字段说明:
- tenant_id / user_id:调用方身份,计量流水按它们隔离;
- key_id / key_prefix:用 API Key 调用时才有值(JWT 调用为 None),用于按 Key 分组统计;
- role:项目角色编码(admin / readonly / super_admin),对应任务书里的 tenant_admin;
- request_id:与结构化日志的 request_id 同源,便于"日志中无模型调用记录"这类走查。
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class RequestContext:
    request_id: str
    tenant_id: int
    user_id: int
    role: str
    key_id: Optional[int] = None
    key_prefix: Optional[str] = None

    @property
    def via_api_key(self) -> bool:
        return self.key_id is not None
