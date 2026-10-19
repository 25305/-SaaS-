import pytest
from sqlalchemy import select

from app.db.tenant_hook import TENANT_BYPASS_KEY
from app.models import Role, Tenant, User
from app.security import hash_password


@pytest.mark.parametrize(
    ("username", "password", "role", "home"),
    [
        ("platform_admin", "Platform@123", "super_admin", "/admin"),
        ("academic_admin", "Academic@123", "admin", "/workbench"),
        ("academic_viewer", "Viewer@123", "readonly", "/workbench"),
    ],
)
def test_login_returns_role_identity(env, username, password, role, home):
    with env["db"]() as db:
        db.info[TENANT_BYPASS_KEY] = True
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if role == "super_admin" and user is None:
            super_role = Role(code="super_admin", name="平台管理员")
            tenant = Tenant(code="platform", name="平台")
            db.add_all([super_role, tenant])
            db.flush()
            user = User(
                tenant_id=tenant.id,
                role_id=super_role.id,
                username=username,
                is_active=True,
            )
            db.add(user)
        elif username == "academic_admin":
            user = db.execute(select(User).where(User.username == "alpha_admin")).scalar_one()
            user.username = username
        elif username == "academic_viewer":
            user = db.execute(select(User).where(User.username == "beta_viewer")).scalar_one()
            user.username = username
        user.password_hash = hash_password(password)
        db.commit()

    response = env["client"].post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["role"] == role
    assert data["home"] == home
    assert data["username"] == username
    assert data["tenant_code"]
