from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantMixin


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # super_admin / admin / readonly
    name: Mapped[str] = mapped_column(String(50))  # 平台管理员 / 租户管理员 / 只读成员


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # ---------------- 配额与计量(模块 C/D) ----------------
    # 统计窗口起点:惰性重置(FR-D5)把"本月是否已重置"的检查放在请求路径上,
    # 不对齐自然月时把它改回当月 1 日。法定/结算周期若需按自定义起始日,直接改本字段即可模拟验证。
    period_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # 月度上限:Token 总量与请求次数双维度(FR-D1)。NULL 表示该维度不限制。
    quota_monthly_tokens: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    quota_monthly_requests: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 超额行为:reject 拒绝(429) / soft_warn 放行但告警(FR-D6)。不允许出现第三种取值。
    overage_policy: Mapped[str] = mapped_column(String(20), nullable=False, default="reject")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 成员关系：users.tenant_id 外键即"一个租户对应多个用户"的成员关系
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    role: Mapped[Role] = relationship("Role", lazy="joined")
    tenant: Mapped[Tenant] = relationship("Tenant", lazy="joined")

    @property
    def role_code(self) -> str:
        return self.role.code


class KnowledgeBase(TenantMixin, Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (Index("ix_kb_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))  # 知识库名称
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # 简介
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Document(TenantMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_doc_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)  # 所属知识库
    title: Mapped[str] = mapped_column(String(200))  # 文档标题
    content: Mapped[str] = mapped_column(Text)  # 正文(问答知识库语料)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UsageRecord(TenantMixin, Base):
    """请求用量流水(模块 B,FR-B4)。

    继承 TenantMixin 即自动获得读写隔离:租户 B 的看板/明细查不到租户 A 的数据。
    api_key_id 预留给模块 C 的 API Key 体系;为 NULL 表示用 JWT 登录态访问。
    """

    __tablename__ = "usage_records"
    __table_args__ = (Index("ix_usage_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    api_key_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 预留:发起调用的 Key
    method: Mapped[str] = mapped_column(String(10))  # HTTP 方法
    path: Mapped[str] = mapped_column(String(200), index=True)  # 请求路径
    status_code: Mapped[int] = mapped_column(Integer)  # 响应状态码
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)  # 耗时(毫秒)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ApiKey(TenantMixin, Base):
    """租户 API Key(模块 C,FR-C1~C6)。

    安全红线(FR-C2):
    - 库里只存 SHA-256 哈希 + 前缀 + 后缀,完整 Key 只在创建/轮换响应中返回一次;
    - 为什么用 SHA-256 而不是 bcrypt:Key 是 32 字节随机的高熵串(输入空间 2^256,
      字典/彩虹表无意义),而鉴权路径每个请求都要校验,必须快;bcrypt 的慢哈希是为
      低熵人手密码设计的,用在 Key 上只增加每请求开销、不增加安全性。

    隔离例外:鉴权阶段还没有租户上下文,只能按全局唯一的 key_hash 反查租户;
    该查询等价于按唯一键取单行,不构成越权,详见 app/services/apikey/service.py。
    """

    __tablename__ = "api_key"
    __table_args__ = (
        Index("ix_apikey_tenant_created", "tenant_id", "created_at"),
        Index("ix_apikey_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)  # 归属成员
    name: Mapped[str] = mapped_column(String(60))  # 用途描述
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256,绝不存明文
    key_prefix: Mapped[str] = mapped_column(String(16))  # 如 sk-abc123
    key_suffix: Mapped[str] = mapped_column(String(8))  # 如 xyz9
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/expiring/revoked
    # 轮换时给旧 Key 设的过期时间(给调用方 24h 灰度切换窗口,FR-C3)
    expire_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # NULL = 继承租户配额;非 NULL 时覆盖租户配额(FR-C4)
    quota_monthly_tokens: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    quota_monthly_requests: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 限流阈值,由限流中间件(成员 D 的 E 模块)读取
    rate_limit_per_minute: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rate_limit_per_hour: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_used_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    @property
    def masked(self) -> str:
        """展示用,如 sk-abc123...xyz9。列表接口只返回它(FR-C2)。"""
        return f"{self.key_prefix}...{self.key_suffix}"


class LlmUsageRecord(TenantMixin, Base):
    """模型调用用量流水(模块 C/D,FR-D2/D4)。

    表名沿用任务书口径 `usage_record`;类名带 Llm 前缀以区别于成员 B 的
    HTTP 访问流水 UsageRecord(表 `usage_records`,只记 path/status_code)。

    【不可变流水账,FR-D4】本表只允许 INSERT:
    - 本文件不提供任何 update/delete/upsert 该表的方法;
    - 代码评审与 tests/test_quota.py 的静态检查都会核对 app/ 下不存在
      对账期流水表的 ORM 批量 DML(例如 `query(...).update(...)`
      或 `execute(update(...))` 这类写法);
    - usage_summary 的聚合值一律由本表 SUM/COUNT 派生,不用可写计数器。

    账期口径:created_at 与 usage_summary.bucket_date、tenant.period_start
    统一使用服务器本地时间(naive),保证"自然月"边界与聚合分桶同源。
    """

    __tablename__ = "usage_record"
    __table_args__ = (
        Index("ix_llmusage_tenant_created", "tenant_id", "created_at"),
        Index("ix_llmusage_key", "key_id"),
        Index("ix_llmusage_request", "request_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # NULL = JWT 登录态调用
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str] = mapped_column(String(64))  # 与日志 request_id 对齐,用于追溯
    model: Mapped[str] = mapped_column(String(64))
    # 均来自模型响应的 usage,不做任何估算(FR-D2);缓存命中记 0
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20))  # success/error/quota_rejected
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class UsageSummary(TenantMixin, Base):
    """按"租户 + 自然日"的用量聚合(FR-D3 预检的数据源)。

    由 `app/services/metering/service.py` 的 refresh_summary() 从 usage_record
    派生并 UPSERT(成员 D 的异步任务可复用同一函数)。

    【字段口径·定稿后不许改】成员 D 的看板与账单导出依赖:
    - requests        = 实际消耗配额的请求数,即 status ∈ (success, error)。
                        quota_rejected 的行只进流水做审计,不计入 requests,
                        否则"被拒的请求"会把用量越推越高,形成拒绝雪崩。
    - success/errors  = status 分别为 success / error 的行数。
    - prompt_tokens / completion_tokens = SUM,缓存命中行贡献 0。
    - avg_latency_ms / p95_latency_ms   = 当日全部流水(含被拒行)的耗时聚合。
    - cache_hits      = cache_hit=1 的行数。
    """

    __tablename__ = "usage_summary"
    __table_args__ = (
        UniqueConstraint("tenant_id", "bucket_date", name="uk_usage_summary_tenant_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bucket_date: Mapped[date] = mapped_column(Date, index=True)  # 自然日
    requests: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    completion_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    avg_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    p95_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cache_hits: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )
