"""一份文档的结构节点视图、按代次加载及续读游标。

节点、阅读单元 ID 均以解析代次开头，只在当前索引内有效；重新解析后旧 ID 明确失效。
"""

import base64
import json
import re

from sqlalchemy import select

from ..db import Document, DocumentIndex

STALE = "节点不存在或索引已更新，请重新搜索或定位"


class Outline:
    def __init__(self, document_id, title, meta, nodes):
        self.document_id, self.title, self.meta, self.nodes = document_id, title, meta, nodes
        self.root = nodes[0]
        self.generation = self.root["id"].split(":")[0]
        self.by_id = {node["id"]: node for node in nodes}
        self.by_unit = {node["unit_id"]: node for node in nodes if node["unit_id"]}

    def node(self, target_id):
        """按节点 ID 或阅读单元 ID 取节点。"""
        node = self.by_id.get(target_id) or self.by_unit.get(target_id)
        if node is None:
            raise ValueError(STALE)
        return node

    def ancestors(self, node):
        result = []
        while node["parent_id"]:
            node = self.by_id[node["parent_id"]]
            result.append(node)
        return result

    def within(self, node, parent_id):
        return node["id"] == parent_id or any(n["id"] == parent_id for n in self.ancestors(node))

    def article(self, node):
        """所属条款：优先“第…条”正式条款，否则取最近的编号项。"""
        chain = [n for n in [node, *self.ancestors(node)] if n["kind"] == "article"]
        return next((n for n in chain if n["label"].startswith("第")), chain[0] if chain else None)

    def citation(self, page):
        return f"/api/documents/{self.document_id}#page={page}"

    def info(self, node):
        path = [*reversed(self.ancestors(node)), node]
        return {"id": self.document_id, "title": self.title, "node_id": node["id"],
                "kind": node["kind"], "label": node["label"], "parent_id": node["parent_id"],
                "path": [n["label"] for n in path], "pages": node["pages"],
                "page": node["pages"][0], "citation": self.citation(node["pages"][0])}

    def select(self, target, scope, neighbors):
        """确定阅读范围内带正文的节点，按原文顺序返回；不做相关性筛选。"""
        if scope == "article":
            target = self.article(target)
            if target is None:
                raise ValueError("未识别所属条款，请读取当前节点或相邻段落")
        if scope == "neighbors":
            if target["kind"] != "paragraph":
                raise ValueError("相邻段落读取仅用于正文段落，请先选择段落节点")
            siblings = [n for n in self.nodes if n["parent_id"] == target["parent_id"]]
            at = siblings.index(target)
            # 只取同一父节点内连续的正文，不越过表格、章节或条款边界。
            selected = [target]
            for direction in (-1, 1):
                for distance in range(1, neighbors + 1):
                    index = at + direction * distance
                    if not 0 <= index < len(siblings) or siblings[index]["kind"] != "paragraph":
                        break
                    selected.append(siblings[index])
            return target, sorted(selected, key=lambda n: n["order"])
        selected = [n for n in self.nodes if n["unit_id"] and self.within(n, target["id"])]
        # 整表单元已含全部行；表格在范围内时不再逐行重复。
        tables = {n["id"] for n in selected if n["kind"] == "table"}
        return target, [n for n in selected
                        if not (n["kind"] == "table_row" and n["parent_id"] in tables)]


async def load_outlines(db, document_ids=None):
    """读取结构节点（不含正文）；document_ids 为 None 时读取全库。"""
    statement = select(DocumentIndex.document_id, Document.title, DocumentIndex.meta,
                       DocumentIndex.nodes).join(Document).order_by(Document.id)
    if document_ids is not None:
        statement = statement.where(DocumentIndex.document_id.in_(document_ids))
    async with db.sessions() as session:
        return {row.document_id: Outline(*row) for row in await session.execute(statement)}


async def find_outline(db, target_id):
    """由节点或阅读单元 ID 的代次前缀找到所属文档。"""
    statement = select(DocumentIndex.document_id, Document.title, DocumentIndex.meta,
                       DocumentIndex.nodes).join(Document).where(
        DocumentIndex.generation == target_id.split(":")[0])
    async with db.sessions() as session:
        row = (await session.execute(statement)).first()
    if row is None:
        raise ValueError(STALE)
    return Outline(*row)


def normalize_label(value):
    return re.sub(r"[\s、，。:：/（）()]+", "", value).casefold()


def encode_cursor(scope, offset):
    return base64.urlsafe_b64encode(json.dumps([scope, offset]).encode()).decode()


def decode_cursor(cursor, scope):
    if not cursor:
        return 0
    try:
        saved_scope, offset = json.loads(base64.urlsafe_b64decode(cursor))
        if saved_scope != scope or type(offset) is not int or offset < 0:
            raise ValueError
        return offset
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("续读游标无效或阅读范围已变化，请重新读取") from None
