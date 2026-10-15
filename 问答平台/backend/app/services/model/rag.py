# -*- coding: utf-8 -*-
"""问答链路(简化版 RAG,模块 C 的 D5/D6 落点)。

成员 D 的正式 RAG 链路接入后,只要仍在返回值里带上模型响应的 `usage`,
本模块的计量代码路径(record_success 的入参)无需改动。

链路顺序严格遵循 D6:**配额预检 → 查缓存 → 调模型**,顺序由 app/api/v1/qa.py 编排。
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.models import Document
from app.services.model.llm_client import (
    LlmClient,
    LlmCompletion,
    get_llm_client,
    tokenize,
)

# 简化检索:问题 token 与文档正文 token 的重叠度打分,取前 N 条作为上下文
RETRIEVAL_LIMIT = 3


def retrieve_context(db: Session, question: str) -> list[str]:
    """从本租户知识库检索上下文片段。

    这里没有手写 tenant_id 条件:ORM 钩子会给 `select(Document)` 自动注入租户条件,
    因此检索天然只在本租户语料内进行(跨租户语料不可能被召回)。
    """
    docs = db.execute(select(Document)).scalars().all()
    terms = set(tokenize(question))
    scored: list[tuple[int, str]] = []
    for doc in docs:
        overlap = len(terms & set(tokenize(doc.title + doc.content)))
        if overlap:
            scored.append((overlap, doc.content))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [content for _, content in scored[:RETRIEVAL_LIMIT]]


def answer(
    db: Session,
    *,
    question: str,
    model: str,
    client: Optional[LlmClient] = None,
) -> tuple[LlmCompletion, list[str]]:
    """检索 + 调模型。返回 (模型响应, 上下文片段)。"""
    context = retrieve_context(db, question)
    completion = (client or get_llm_client()).complete(
        question=question, context=context, model=model
    )
    if completion.usage is None:
        # 绝不估算:模型没返回 usage 就记 0 并告警,宁可少记也不能猜(FR-D2)
        logger.error(
            "llm_usage_missing",
            extra={"fields": {"model": completion.model, "provider": completion.provider}},
        )
    return completion, context
