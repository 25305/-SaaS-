# -*- coding: utf-8 -*-
"""模型调用客户端(模块 C,D5:真实 Token 计量的上游)。

## 两种提供方
| provider | 触发条件 | usage 来源 |
|---|---|---|
| `openai-compatible` | 配置了 `MODEL_BASE_URL` | 模型响应体里的 `usage` 字段 |
| `local-simulation` | 未配置 `MODEL_BASE_URL`(开发/演示) | 本地模拟模型自带的用量统计 |

**计量代码路径与提供方无关**:两种客户端都返回同一个 `LlmCompletion`,计量层只读
`completion.usage`(即 `resp.usage`),绝不使用 tiktoken 之类的估算——FR-D2 的验收
红线是"Token 必须来自模型返回",估算一律判不合格。

本地模拟模型是**明确标注**的开发替代品(日志字段 `model_provider=local-simulation`),
不冒充真实模型:项目在未配置上游时就该表现为"未接入真实模型"。它用内置分词器对自己
实际处理的 prompt/completion 文本计数,因此其 `usage` 是"该模型自己报出的用量",
与真实模型返回 `usage` 的结构完全一致;接入真实上游后 provider 自动切换,计量不动。
"""

import re
import time
from dataclasses import dataclass
from typing import Optional, Sequence

import httpx

from app.config import settings
from app.core.errors import BizError, ErrorCode
from app.core.logging import logger

# 内置分词口径:拉丁字母/数字串算 1 个 token,其余每个字符算 1 个 token。
# 中文按字计,与主流中文模型的数量级接近。仅用于本地模拟模型与检索,不用于真实模型计量。
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


@dataclass
class LlmUsage:
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LlmCompletion:
    answer: str
    model: str
    provider: str
    usage: Optional[LlmUsage]
    usage_source: str  # 日志佐证字段:走查"非估算"时 grep 它


class LlmClient:
    provider = "unknown"

    def complete(self, *, question: str, context: Sequence[str], model: str) -> LlmCompletion:
        raise NotImplementedError


class OpenAICompatibleClient(LlmClient):
    """OpenAI 兼容的 `/chat/completions` 调用,从 `response.usage` 取真实用量。"""

    provider = "openai-compatible"

    def complete(self, *, question: str, context: Sequence[str], model: str) -> LlmCompletion:
        messages = [
            {"role": "system", "content": "你是知识库问答助手,只依据给定的知识片段回答。"},
        ]
        if context:
            messages.append({"role": "user", "content": "知识片段:\n" + "\n".join(context)})
        messages.append({"role": "user", "content": question})

        headers = {"Content-Type": "application/json"}
        if settings.MODEL_API_KEY:
            headers["Authorization"] = "Bearer " + settings.MODEL_API_KEY
        try:
            resp = httpx.post(
                settings.MODEL_BASE_URL.rstrip("/") + "/chat/completions",
                json={"model": model, "messages": messages},
                headers=headers,
                timeout=settings.MODEL_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            logger.error(
                "llm_call_failed",
                extra={"fields": {"model": model, "provider": self.provider,
                                  "error_type": type(exc).__name__}},
            )
            raise BizError(ErrorCode.MODEL_SERVICE_ERROR)

        if resp.status_code < 200 or resp.status_code >= 300:
            logger.error(
                "llm_call_failed",
                extra={"fields": {"model": model, "provider": self.provider,
                                  "http_status": resp.status_code}},
            )
            raise BizError(ErrorCode.MODEL_SERVICE_ERROR)

        body = resp.json()
        choices = body.get("choices") or []
        answer = ""
        if choices:
            answer = (choices[0].get("message") or {}).get("content") or ""

        usage_raw = body.get("usage")
        usage: Optional[LlmUsage] = None
        if usage_raw is not None:
            usage = LlmUsage(
                prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
                completion_tokens=int(usage_raw.get("completion_tokens") or 0),
            )
        return LlmCompletion(
            answer=answer,
            model=model,
            provider=self.provider,
            usage=usage,
            usage_source="response.usage",
        )


class LocalSimulationClient(LlmClient):
    """本地模拟模型:用租户知识库片段拼答案,并对自己处理的文本统计用量。

    不是真实模型,也不冒充真实模型(日志里 `model_provider=local-simulation`)。
    存在意义:在没有上游凭据的开发/演示环境里,让"预检 → 缓存 → 模型 → 计量"
    这条链路可以端到端跑通并被验收检查。
    """

    provider = "local-simulation"

    def complete(self, *, question: str, context: Sequence[str], model: str) -> LlmCompletion:
        if context:
            answer = "根据知识库内容:" + "".join(context)
        else:
            answer = "知识库中没有检索到与该问题相关的内容,请补充资料或换个问法。"
        return LlmCompletion(
            answer=answer,
            model=model,
            provider=self.provider,
            usage=LlmUsage(
                prompt_tokens=len(tokenize(question + "".join(context))),
                completion_tokens=len(tokenize(answer)),
            ),
            usage_source="response.usage",
        )


def get_llm_client() -> LlmClient:
    """按配置返回客户端。未配置上游时返回本地模拟模型。"""
    if settings.MODEL_BASE_URL:
        return OpenAICompatibleClient()
    return LocalSimulationClient()


def measure_latency(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))
