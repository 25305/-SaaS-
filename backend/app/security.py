import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------------------------------------------------
# 密码
# ---------------------------------------------------------
def hash_password(plain: str) -> str:
    """bcrypt 加盐哈希，严禁明文/可逆加密。"""
    return pwd_context.hash(plain)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

# ---------------------------------------------------------
# JWT
# ---------------------------------------------------------
def create_access_token(
    *,
    user_id: int,
    tenant_id: Optional[int],
    role: Optional[str],
    is_super_admin: bool = False,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tenant_id": tenant_id,
        "role": role,
        "super": is_super_admin,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

def decode_token(token: str) -> dict:
    """校验签名与过期时间，失败抛 jwt.PyJWTError。"""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])

# ---------------------------------------------------------
# 登出黑名单（D2 用进程内集合；生产换 Redis）
# ---------------------------------------------------------
_revoked_jti: set[str] = set()

def revoke_jti(jti: str) -> None:
    _revoked_jti.add(jti)

def is_revoked(jti: str) -> bool:
    return jti in _revoked_jti