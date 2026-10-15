# -*- coding: utf-8 -*-
"""API Key 生成与哈希工具(模块 C,D2/FR-C2)。

核心红线:**完整 Key 只在创建/轮换响应中返回一次,绝不落库**。
库里只有 `key_hash`(SHA-256)、`key_prefix`、`key_suffix` 三样东西。
"""

import hashlib
import secrets

PREFIX_LEN = 11  # sk- + 8 位
SUFFIX_LEN = 4
# 32 字节随机 → 输入空间 2^256,无法字典枚举,故 SHA-256 足够(见 models.ApiKey 的论证)
TOKEN_BYTES = 32


def generate_api_key() -> tuple[str, str, str, str]:
    """返回 (完整值, 哈希, 前缀, 后缀)。

    完整值只用于创建/轮换的响应体,调用方保存后服务端不再持有。
    """
    raw = "sk-" + secrets.token_urlsafe(TOKEN_BYTES)
    return raw, hash_key(raw), raw[:PREFIX_LEN], raw[-SUFFIX_LEN:]


def hash_key(raw: str) -> str:
    """SHA-256 十六进制摘要(64 位)。

    这里刻意不用 bcrypt:密码是低熵的人造字符串,需要慢哈希抬高校验成本;
    API Key 是高熵随机串,慢哈希没有额外收益,反而拖慢每次调用。
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mask(prefix: str, suffix: str) -> str:
    """展示用,如 sk-abc123...xyz9。任何列表/详情响应都只返回它。"""
    return f"{prefix}...{suffix}"
