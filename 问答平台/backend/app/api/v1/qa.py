# -*- coding: utf-8 -*-
"""问答接口(模块 D,D6 ★ P0 生死线)。

## 三步顺序不可调换
1. **配额预检** —— 此时绝不调用模型。超限按租户策略拒绝(429)或告警放行;
2. **查缓存** —— 命中则只记一条 `cache_hit=1, tokens=0` 的流水,不调模型;
3. **调模型** —— 只有走到这里才会产生模型调用与 Token 成本。

任何"先调模型再判断配额"的写法都会让"未调用模型"这条硬性验收失分,并且真的白白
烧掉上游 Token。测试 `tests/test_quota.py::test_quota_rejected_without_model_call`
用计数器断言拒绝路径没有调用模型。

## 日志佐证
`llm_call_completed` 里带 `usage_source`(= 模型响应 usage 的来源标注)与
`model_provider`(local-simulation / openai-compatible),
验收时 `grep usage_source` 即可证明 Token 不是估算出来的(FR-D2)。
"""

import time
from typing import Optional

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.core.context import RequestContext
from app.core.errors import BizError, QuotaExceededError
from app.core.logging import logger
from app.core.response import ok
from app.db.session import get_db
from app.deps import get_current_context
from app.schemas import AskIn
from app.services.cache.backend import get_cache, tenant_key
from app.services.metering import service as metering
from app.services.model import rag
from app.services.model.llm_client import LlmUsage, measure_latency
from app.services.quota import service as quota_service

router = APIRouter(prefix="/api/v1/qa", tags=["问答"])

QUOTA_WARNING_HEADER = "X-Quota-Warning"


@router.post("/ask", summary="知识库问答(配额预检 → 缓存 → 模型)")
def ask(
    payload: AskIn,
    response: Response,
    ctx: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    model = payload.model or settings.MODEL_NAME
    question = payload.question.strip()

    # ── 第 1 步:配额预检(此时绝不调用模型) ──────────────────────────
    quota = quota_service.check(db, ctx)
    if not quota.allowed:
        fields = {
            "request_id": ctx.request_id,
            "tenant_id": ctx.tenant_id,
            "key_prefix": ctx.key_prefix,
            "used_tokens": quota.used_tokens,
            "limit_tokens": quota.limit_tokens,
            "used_requests": quota.used_requests,
            "limit_requests": quota.limit_requests,
        }
        if quota.policy == quota_service.POLICY_SOFT_WARN:
            # 软超额:放行,但必须让调用方看得见(响应头 + 日志)
            logger.warning("quota_soft_overage", extra={"fields": fields})
            response.headers[QUOTA_WARNING_HEADER] = "overage"
        else:
            # 拒绝:先记一条 quota_rejected 流水(0 Token / 0 耗时)再抛错,
            # 拒绝也有审计痕迹,且该状态不计入配额消耗,避免拒绝雪崩。
            logger.warning("quota_rejected", extra={"fields": fields})
            metering.record_quota_rejected(db, ctx, model=model)
            raise QuotaExceededError(
                used_tokens=quota.used_tokens,
                limit_tokens=quota.limit_tokens,
                used_requests=quota.used_requests,
                limit_requests=quota.limit_requests,
                reset_at=quota.reset_at.isoformat(),
            )

    # ── 第 2 步:查缓存(缓存 key 带租户前缀,杜绝跨租户串数据) ────────
    cache = get_cache()
    cache_key = tenant_key(ctx.tenant_id, "qa", question)
    cached = cache.get(cache_key)
    if cached is not None:
        metering.record_cache_hit(db, ctx, model=model)
        logger.info(
            "qa_cache_hit",
            extra={"fields": {"request_id": ctx.request_id, "tenant_id": ctx.tenant_id,
                              "key_prefix": ctx.key_prefix, "model": model}},
        )
        return ok({
            "answer": cached,
            "model": model,
            "cache_hit": True,
            "tokens": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "latency_ms": 0,
        })

    # ── 第 3 步:调模型(只有到这里才产生模型调用) ────────────────────
    started = time.perf_counter()
    try:
        completion, context = rag.answer(db, question=question, model=model)
    except BizError:
        metering.record_error(db, ctx, model=model, latency_ms=measure_latency(started))
        raise
    latency_ms = measure_latency(started)

    usage: Optional[LlmUsage] = completion.usage
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0

    # Token 来源佐证:usage_source 标明取自模型响应(非估算),便于验收 grep
    logger.info(
        "llm_call_completed",
        extra={"fields": {
            "request_id": ctx.request_id,
            "tenant_id": ctx.tenant_id,
            "key_prefix": ctx.key_prefix,
            "model": completion.model,
            "model_provider": completion.provider,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "context_chunks": len(context),
            "latency_ms": latency_ms,
            "usage_source": completion.usage_source,
        }},
    )

    metering.record_success(
        db, ctx, model=completion.model,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        latency_ms=latency_ms,
    )
    cache.set(cache_key, completion.answer, ttl=settings.QA_CACHE_TTL_SECONDS)

    return ok({
        "answer": completion.answer,
        "model": completion.model,
        "model_provider": completion.provider,
        "cache_hit": False,
        "tokens": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "latency_ms": latency_ms,
    })
