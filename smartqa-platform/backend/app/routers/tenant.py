from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.core.response import ok
from app.deps import get_current_user, get_db, require_admin
from app.models import Role, User
from app.schemas import MemberCreate, MemberInfo, MemberUpdate
from app.security import hash_password

router = APIRouter(prefix="/api/tenant", tags=["租户管理"])


@router.get("/users", summary="成员列表", description="列出本租户的所有成员，本租户任意成员可查看。")
def list_members(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    users = db.execute(
        select(User).where(User.tenant_id == current_user.tenant_id).order_by(User.id)
    ).scalars().all()
    return ok(
        [
            MemberInfo(
                id=u.id,
                username=u.username,
                email=u.email,
                role=u.role_code,
                is_active=u.is_active,
            )
            for u in users
        ]
    )


@router.post("/users", summary="创建成员", description="在本租户下创建成员（租户管理员操作）。")
def create_member(
    req: MemberCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if db.execute(select(User.id).where(User.username == req.username)).first():
        raise BizError("USERNAME_EXISTS", "用户名已存在", 409)
    role = db.execute(select(Role).where(Role.code == req.role)).scalar_one()
    db.add(
        User(
            tenant_id=current_user.tenant_id,
            username=req.username,
            password_hash=hash_password(req.password),
            email=req.email,
            role_id=role.id,
            is_active=True,
        )
    )
    db.commit()
    return ok(message="成员创建成功")


@router.patch("/users/{user_id}", summary="修改成员", description="修改本租户成员的角色或启用状态（租户管理员操作）。")
def update_member(
    user_id: int,
    req: MemberUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if req.role is None and req.is_active is None:
        raise BizError("VALIDATION_ERROR", "至少填写一项要修改的内容", 422)
    if user_id == current_user.id:
        # 防止租户最后一个管理员把自己降级/禁用导致锁死
        raise BizError("FORBIDDEN", "不能修改自己的角色或状态", 403)
    target = db.get(User, user_id)
    if target is None or target.tenant_id != current_user.tenant_id:
        # 跨租户与不存在返回同样的 404，不泄露资源存在性
        raise BizError("USER_NOT_FOUND", "成员不存在", 404)
    if req.role is not None:
        role = db.execute(select(Role).where(Role.code == req.role)).scalar_one()
        target.role_id = role.id
    if req.is_active is not None:
        target.is_active = req.is_active
    db.commit()
    return ok(message="修改成功")
