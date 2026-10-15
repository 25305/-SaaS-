"""知识库与文档接口(模块 B 数据隔离的展示点)。

注意:本文件里所有查询都【没有】手写 tenant_id 条件——
租户条件由 app/db/tenant_hook.py 的 ORM 事件统一注入(FR-B2 要求)。
因此别的租户拿自己的 token 来访问本租户的资源 ID 时,
查询结果同样是"查不到" → 与"资源不存在"返回完全相同的 404,
不泄露资源是否存在(FR-B3)。
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.core.response import ok
from app.deps import get_current_user, get_db, require_admin
from app.models import Document, KnowledgeBase, User
from app.schemas import DocCreate, DocInfo, DocUpdate, KBCreate, KBInfo, KBUpdate

router = APIRouter(prefix="/api/kb", tags=["知识库"])


def _get_own_kb(kb_id: int, db: Session) -> KnowledgeBase:
    """统一获取"本租户自己的知识库"。

    where 里只有 id 条件,租户条件由 tenant_hook 的 ORM 事件自动注入:
    本租户查自己的 → 正常返回;别的租户查这个 id → 同样是 None → 404,
    响应里不带任何资源信息,不泄露存在性(FR-B3)。
    """
    kb = db.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    ).scalar_one_or_none()
    if kb is None:
        raise BizError("KB_NOT_FOUND", "知识库不存在", 404)
    return kb


def _get_own_doc(kb: KnowledgeBase, doc_id: int, db: Session) -> Document:
    """文档同理:先拿到本租户的知识库(跨租户在这里已经 404),再按 id 取文档。"""
    doc = db.execute(
        select(Document).where(Document.id == doc_id, Document.kb_id == kb.id)
    ).scalar_one_or_none()
    if doc is None:
        raise BizError("DOC_NOT_FOUND", "文档不存在", 404)
    return doc


@router.get("/", summary="知识库列表", description="列出本租户自己的知识库及文档数量(任意成员)。")
def list_kbs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(KnowledgeBase).order_by(KnowledgeBase.id)
    ).scalars().all()
    # 统计每个知识库的文档数;这条聚合查询同样被自动注入租户条件
    counts = dict(
        db.execute(
            select(Document.kb_id, func.count(Document.id)).group_by(Document.kb_id)
        ).all()
    )
    return ok(
        [
            KBInfo(
                id=kb.id,
                name=kb.name,
                description=kb.description,
                doc_count=counts.get(kb.id, 0),
                created_at=kb.created_at,
            )
            for kb in rows
        ]
    )


@router.post("/", summary="创建知识库", description="创建本租户的知识库(租户管理员)。")
def create_kb(
    req: KBCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    kb = KnowledgeBase(
        tenant_id=current_user.tenant_id,  # 显式填写;漏填也会被 before_flush 自动补全
        name=req.name,
        description=req.description,
    )
    db.add(kb)
    db.flush()  # 先 flush 拿到自增 id(before_flush 守卫在此执行)
    new_id = kb.id
    db.commit()
    return ok({"id": new_id}, message="知识库创建成功")


@router.get("/{kb_id}", summary="知识库详情", description="查看本租户的知识库(任意成员)。")
def get_kb(
    kb_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    kb = _get_own_kb(kb_id, db)
    count = db.execute(
        select(func.count(Document.id)).where(Document.kb_id == kb.id)
    ).scalar_one()
    return ok(
        KBInfo(
            id=kb.id,
            name=kb.name,
            description=kb.description,
            doc_count=count,
            created_at=kb.created_at,
        )
    )


@router.patch("/{kb_id}", summary="修改知识库", description="改名或改简介(租户管理员)。")
def update_kb(
    kb_id: int,
    req: KBUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if req.name is None and req.description is None:
        raise BizError("VALIDATION_ERROR", "name 和 description 至少填写一项", 422)
    kb = _get_own_kb(kb_id, db)
    if req.name is not None:
        kb.name = req.name
    if req.description is not None:
        kb.description = req.description
    db.commit()
    return ok(message="修改成功")


@router.delete("/{kb_id}", summary="删除知识库", description="删除知识库及其全部文档(租户管理员)。")
def delete_kb(
    kb_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    kb = _get_own_kb(kb_id, db)
    docs = db.execute(
        select(Document).where(Document.kb_id == kb.id)
    ).scalars().all()
    for doc in docs:  # 先删子文档满足外键;每个对象都经过 before_flush 守卫
        db.delete(doc)
    db.delete(kb)
    db.commit()
    return ok(message="知识库已删除")


@router.get("/{kb_id}/docs", summary="文档列表", description="列出知识库下的文档(任意成员)。")
def list_docs(
    kb_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    kb = _get_own_kb(kb_id, db)
    docs = db.execute(
        select(Document).where(Document.kb_id == kb.id).order_by(Document.id)
    ).scalars().all()
    return ok(
        [
            DocInfo(
                id=doc.id,
                kb_id=doc.kb_id,
                title=doc.title,
                content=doc.content,
                created_at=doc.created_at,
            )
            for doc in docs
        ]
    )


@router.post("/{kb_id}/docs", summary="添加文档", description="向知识库添加文档(租户管理员)。")
def create_doc(
    kb_id: int,
    req: DocCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    kb = _get_own_kb(kb_id, db)
    doc = Document(
        kb_id=kb.id,
        tenant_id=current_user.tenant_id,
        title=req.title,
        content=req.content,
    )
    db.add(doc)
    db.flush()
    new_id = doc.id
    db.commit()
    return ok({"id": new_id}, message="文档添加成功")


@router.get("/{kb_id}/docs/{doc_id}", summary="文档详情", description="查看文档内容(任意成员)。")
def get_doc(
    kb_id: int,
    doc_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    kb = _get_own_kb(kb_id, db)
    doc = _get_own_doc(kb, doc_id, db)
    return ok(
        DocInfo(
            id=doc.id,
            kb_id=doc.kb_id,
            title=doc.title,
            content=doc.content,
            created_at=doc.created_at,
        )
    )


@router.patch("/{kb_id}/docs/{doc_id}", summary="修改文档", description="改标题或正文(租户管理员)。")
def update_doc(
    kb_id: int,
    doc_id: int,
    req: DocUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if req.title is None and req.content is None:
        raise BizError("VALIDATION_ERROR", "title 和 content 至少填写一项", 422)
    kb = _get_own_kb(kb_id, db)
    doc = _get_own_doc(kb, doc_id, db)
    if req.title is not None:
        doc.title = req.title
    if req.content is not None:
        doc.content = req.content
    db.commit()
    return ok(message="修改成功")


@router.delete("/{kb_id}/docs/{doc_id}", summary="删除文档", description="删除文档(租户管理员)。")
def delete_doc(
    kb_id: int,
    doc_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    kb = _get_own_kb(kb_id, db)
    doc = _get_own_doc(kb, doc_id, db)
    db.delete(doc)
    db.commit()
    return ok(message="文档已删除")
