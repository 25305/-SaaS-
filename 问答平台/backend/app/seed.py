from datetime import date

from sqlalchemy import select

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import Document, KnowledgeBase, Role, Tenant, User
from app.security import hash_password
from app.db.tenant_hook import TENANT_BYPASS_KEY

# 保留租户编码：注册接口不允许占用
RESERVED_TENANT_CODES = {"platform"}

# 演示租户的兜底配额(模块 D):只在字段为 NULL 时填,不覆盖管理员已调整过的值
DEFAULT_QUOTA_TOKENS = 1_000_000
DEFAULT_QUOTA_REQUESTS = 10_000

ROLES = [
    ("super_admin", "平台管理员"),
    ("admin", "租户管理员"),
    ("readonly", "只读成员"),
]

# 演示账号清单（验收用）：(租户编码, 租户名, 用户名, 密码, 角色编码)
DEMO_ACCOUNTS = [
    ("platform", "平台", "platform_admin", "Platform@123", "super_admin"),
    ("academic", "学术问答", "academic_admin", "Academic@123", "admin"),
    ("academic", "学术问答", "academic_viewer", "Viewer@123", "readonly"),
    ("physics", "物理问答", "physics_admin", "Physics@123", "admin"),
]

# 演示知识库：(租户编码, 知识库名, 简介, [(文档标题, 文档正文), ...])
# 内容为真实语义的中文知识文本(课程演示数据,非占位符),详见 docs/数据来源清单.md
KB_SEEDS = [
    ("academic", "图书馆借阅规则", "图书馆借阅相关规则说明(课程演示数据)", [
        ("借阅须知", "读者凭本人校园卡到总服务台办理借阅手续。本科生单次最多借阅 10 册,借期为 30 天,到期前可通过图书馆网站续借一次,续借期为 30 天。"),
        ("超期处理办法", "图书超期未还将暂停借阅权限,并按每册每天 0.1 元收取滞纳金。滞纳金累计超过 10 元时,需到总服务台缴清后方可恢复借阅。"),
        ("馆际互借说明", "本校未收藏的图书可申请馆际互借,读者在图书馆网站提交申请,馆员在 3 个工作日内反馈受理结果,互借图书借期一般为 30 天且不可续借。"),
    ]),
    ("academic", "教务处选课须知", "选课、退课与补选的相关规定(课程演示数据)", [
        ("选课时间安排", "每学期选课分为三轮:第一轮抽签、第二轮抢课、第三轮补退选。具体时间以教务处每学期发布的通知为准,选课系统在每日 8:00 至 22:00 开放。"),
        ("退课与补选规则", "开学后前两周内可自行在选课系统退课,退课名额实时释放。因课程冲突确需补选的,需在第三轮补退选期间提交申请,由开课学院审批。"),
    ]),
    ("physics", "牛顿力学基础", "经典力学的三大定律(课程演示数据)", [
        ("牛顿第一定律", "任何物体在不受外力或所受合外力为零时,总保持静止状态或匀速直线运动状态,直到有外力迫使它改变这种状态为止,这一定律又称惯性定律。"),
        ("牛顿第二定律", "物体的加速度与所受合外力成正比,与物体质量成反比,方向与合外力方向相同,数学表达式为 F=ma。"),
        ("牛顿第三定律", "两个物体之间的作用力与反作用力总是大小相等、方向相反,作用在同一条直线上,且分别作用在两个不同的物体上。"),
    ]),
    ("physics", "物理学史", "物理学发展史上的重要人物与事件(课程演示数据)", [
        ("伽利略与近代科学", "伽利略通过斜面实验与自由落体研究,开创了以实验为基础的近代科学方法,提出惯性思想,被誉为近代物理学之父。"),
        ("爱因斯坦与相对论", "1905 年爱因斯坦发表狭义相对论,提出光速不变原理与相对性原理;1915 年完成广义相对论,将引力解释为时空弯曲。"),
    ]),
]


def run_seed() -> None:
    """幂等写入角色、演示租户与账号；每次运行重置其密码/角色，保证演示账号可用。"""
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        # seed 是经过审核的跨租户维护入口;仅限此独立 Session,不复用请求 Session。
        db.info[TENANT_BYPASS_KEY] = True
        # 1. 角色
        roles: dict[str, Role] = {}
        for code, name in ROLES:
            role = db.execute(select(Role).where(Role.code == code)).scalar_one_or_none()
            if role is None:
                role = Role(code=code, name=name)
                db.add(role)
                db.flush()
            roles[code] = role

        # 2. 租户
        tenants: dict[str, Tenant] = {}
        for tenant_code, tenant_name, *_ in DEMO_ACCOUNTS:
            if tenant_code in tenants:
                continue
            tenant = db.execute(
                select(Tenant).where(Tenant.code == tenant_code)
            ).scalar_one_or_none()
            if tenant is None:
                tenant = Tenant(code=tenant_code, name=tenant_name)
                db.add(tenant)
                db.flush()
            tenants[tenant_code] = tenant

        # 3. 用户（每次运行强制对齐角色/密码/状态，防数据漂移；不要在这里 flush，
        #    否则新建用户会在 password_hash 赋值前被 INSERT，触发非空约束）
        for tenant_code, _name, username, password, role_code in DEMO_ACCOUNTS:
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is None:
                user = User(
                    tenant_id=tenants[tenant_code].id,
                    username=username,
                    email=None,
                    is_active=True,
                )
                db.add(user)
            user.tenant_id = tenants[tenant_code].id
            user.role_id = roles[role_code].id
            user.password_hash = hash_password(password)
            user.is_active = True

        # 4. 知识库与文档(幂等:同租户下同名知识库/文档已存在则跳过,不重复、不覆盖)
        # seed 独立 Session 上已显式声明授权绕过;常规请求缺上下文时业务表会被拒绝
        for tenant_code, kb_name, kb_desc, docs in KB_SEEDS:
            kb = db.execute(
                select(KnowledgeBase).where(
                    KnowledgeBase.tenant_id == tenants[tenant_code].id,
                    KnowledgeBase.name == kb_name,
                )
            ).scalar_one_or_none()
            if kb is None:
                kb = KnowledgeBase(
                    tenant_id=tenants[tenant_code].id, name=kb_name, description=kb_desc
                )
                db.add(kb)
                db.flush()
            for title, content in docs:
                exists = db.execute(
                    select(Document).where(Document.kb_id == kb.id, Document.title == title)
                ).scalar_one_or_none()
                if exists is None:
                    db.add(
                        Document(
                            kb_id=kb.id, tenant_id=kb.tenant_id, title=title, content=content
                        )
                    )

        # 5. 配额兜底(模块 C/D):为空的维度填默认值,使演示租户开箱即有真实配额
        for tenant in tenants.values():
            if tenant.period_start is None:
                tenant.period_start = date.today().replace(day=1)
            if tenant.quota_monthly_tokens is None:
                tenant.quota_monthly_tokens = DEFAULT_QUOTA_TOKENS
            if tenant.quota_monthly_requests is None:
                tenant.quota_monthly_requests = DEFAULT_QUOTA_REQUESTS
        db.commit()
