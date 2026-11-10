"""结构定位与确定性阅读；节点 ID 只在一次解析快照内有效。"""

import base64
import json
import re
from uuid import uuid4

from .pdf_structure import article_label, heading_level
from .pdf_tables import describe_tables

READ_BUDGET = 6000
LOCATE_LIMIT = 30


def build_structure(parsed, preview, title, filename):
    generation = uuid4().hex
    nodes = []

    def add(kind, label, parent=None, content="", pages=None, spans=None):
        node = {"id": f"{generation}:{len(nodes)}", "kind": kind, "label": label,
                "parent_id": parent, "order": len(nodes), "content": content,
                "pages": pages or [], "source_pages": pages or [],
                "spans": spans or [], "warnings": [],
                "boundary_status": "confirmed", "boundary_reasons": []}
        nodes.append(node)
        return node

    root = add("document", title)
    headings, formal, item = {}, None, None
    tables = {t["section_id"]: t for t in describe_tables(parsed["sections"], title)}
    chunks_by_section = {}
    for chunk in preview["chunks"]:
        chunks_by_section.setdefault(chunk["section_id"], []).append(chunk)
    for section in parsed["sections"]:
        content = section["content"].strip()
        level = heading_level(content)
        label = article_label(content) if section["kind"] != "table" else ""
        if section["kind"] == "document_title":
            continue
        if section["kind"] == "effect" or content.startswith(("附件：", "附件:")):
            headings, formal, item = {}, None, None
        elif not formal and content.startswith(("本通知", "特此通知")):
            headings, item = {}, None
        if level is not None and section["kind"] != "table":
            headings = {k: v for k, v in headings.items() if k < level}
            if level < 3:
                formal, item = None, None
            else:
                item = None
            parent = headings[max(headings)]["id"] if headings else root["id"]
            if formal and (not headings or max(headings) < 3):
                parent = formal["id"]
            node = add("chapter", content, parent, content, section["pages"])
            headings[level] = node
            continue
        if label.startswith("第"):
            headings = {k: v for k, v in headings.items() if k < 3}
            parent = headings[max(headings)]["id"] if headings else root["id"]
            formal = add("article", label, parent, pages=section["pages"])
            item = None
        parent = headings[max(headings)]["id"] if headings else root["id"]
        if formal and (not headings or max(headings) < 3):
            parent = formal["id"]
        if label and not label.startswith("第"):
            item = add("article", label, parent, pages=section["pages"])
        if item:
            parent = item["id"]
        if section["pure_heading"]:
            if formal and label.startswith("第"):
                formal["content"] = content
            elif item:
                item["content"] = content
            continue
        table = tables.get(section["id"])
        if table:
            table_node = add("table", table["title"], parent, pages=section["pages"])
            units = [(u, add("table_row", f"第 {u['row']} 行", table_node["id"],
                             u["content"], [u["page"]])) for u in table["rows"]]
            for _, node in units:
                node["header_pages"] = table["header_pages"]
                node["table"] = {"id": f"{generation}:{table['table_id']}",
                                 "row": _["row"], "row_key": _["row_key"],
                                 "unit": table["unit"], "fields": _["fields"],
                                 "unit_sources": table["unit_sources"],
                                 "header_paths": table["headers"], "header_cells": table["header_cells"],
                                 "header_pages": table["header_pages"],
                                 "header_status": "inferred", "continuation": table["continuation"]}
                node["warnings"] = ["表格字段、单位及续页表头为解析结果，请核对原文"]
        else:
            spans = [{k: s[k] for k in ("start", "end", "page")}
                     for s in section["source_spans"]]
            units = [({}, add("paragraph", section["title"], parent,
                              section["content"], section["pages"], spans))]
        for unit, node in units:
            node["boundary_status"] = section.get("boundary_status", "uncertain")
            node["boundary_reasons"] = section.get("boundary_reasons", ["缺少解析边界证据"])
            node["unit_kind"] = ("table_row" if table else "question_answer"
                                 if section["kind"] == "article" and "？" in section["content"] else
                                 "article_body" if section["kind"] == "article" else "paragraph")
            node["source_block_ids"] = section.get("block_ids", [])
            node["boundary_evidence"] = {"start": section.get("start_boundary"),
                                         "end": section.get("end_boundary")}
            for chunk in chunks_by_section.get(section["id"], []):
                if table and chunk["row"] != unit["row"]:
                    continue
                chunk["node_id"] = node["id"]
                node["warnings"] = list(dict.fromkeys(node["warnings"] + chunk["warnings"]))
    # 自底向上汇总页码，结构节点保留自己的标题页及所有子节点来源。
    by_id = {n["id"]: n for n in nodes}
    for node in reversed(nodes[1:]):
        parent = by_id[node["parent_id"]]
        parent["pages"] = sorted(set(parent["pages"]) | set(node["pages"]))
        if node["boundary_status"] != "confirmed":
            parent["boundary_status"] = node["boundary_status"]
            parent["boundary_reasons"] = list(dict.fromkeys(parent["boundary_reasons"] + node["boundary_reasons"]))
    reading_units = []
    for node in nodes:
        content = node.pop("content")
        spans = node.pop("spans")
        node["unit_id"] = None
        if content:
            unit_id = f"{generation}:u{len(reading_units)}"
            node["unit_id"] = unit_id
            reading_units.append({"id": unit_id, "node_id": node["id"], "content": content,
                                  "spans": spans, "kind": node.get("unit_kind", node["kind"]),
                                  "source_block_ids": node.get("source_block_ids", []),
                                  "table": node.get("table")})
    for chunk in preview["chunks"]:
        node = by_id[chunk["node_id"]]
        chunk["unit_id"] = node["unit_id"]
        chunk["boundary_status"] = node["boundary_status"]
        chunk["boundary_reasons"] = node["boundary_reasons"]
    return {"generation": generation, "filename": filename, "nodes": nodes,
            "reading_units": reading_units}


def get_node(structure, node_id):
    node = next((n for n in structure["nodes"] if n["id"] == node_id), None)
    if node is None:
        raise ValueError("节点不存在或索引已更新，请重新搜索或定位")
    return node


def reading_content(structure, node):
    unit = next((u for u in structure["reading_units"] if u["id"] == node["unit_id"]), None)
    return {**node, "content": unit["content"] if unit else "",
            "spans": unit["spans"] if unit else [], "table": unit["table"] if unit else None}


def ancestors(structure, node):
    by_id = {n["id"]: n for n in structure["nodes"]}
    result = []
    while node["parent_id"]:
        node = by_id[node["parent_id"]]
        result.append(node)
    return result


def within(structure, node, parent_id):
    return node["id"] == parent_id or any(n["id"] == parent_id for n in ancestors(structure, node))


def node_info(structure, node, document_id, title):
    path = list(reversed(ancestors(structure, node))) + [node]
    return {"id": document_id, "title": title, "node_id": node["id"],
            "kind": node["kind"], "label": node["label"], "parent_id": node["parent_id"],
            "path": [n["label"] for n in path], "pages": node["pages"],
            "page": node["pages"][0],
            "citation": f"/api/documents/{document_id}#page={node['pages'][0]}"}


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


def normalize_label(value):
    return re.sub(r"[\s、，。:：/（）()]+", "", value).casefold()


def read_nodes(structure, target, scope, neighbors):
    if scope == "article":
        article = next((n for n in [target] + ancestors(structure, target)
                        if n["kind"] == "article" and n["label"].startswith("第")), None)
        article = article or next((n for n in [target] + ancestors(structure, target)
                                   if n["kind"] == "article"), None)
        if article is None:
            raise ValueError("未识别所属条款，请读取当前节点或相邻段落")
        target = article
    if scope == "neighbors":
        if target["kind"] != "paragraph":
            raise ValueError("相邻段落读取仅用于正文段落，请先选择段落节点")
        siblings = [n for n in structure["nodes"]
                    if n["parent_id"] == target["parent_id"]]
        at = siblings.index(target)
        # 只取同一父节点内连续的正文，不越过表格、章节或条款边界。
        selected = [target]
        for direction in (-1, 1):
            for distance in range(1, neighbors + 1):
                index = at + direction * distance
                if not 0 <= index < len(siblings) or siblings[index]["kind"] != "paragraph":
                    break
                selected.append(siblings[index])
        return target, [reading_content(structure, n) for n in sorted(selected, key=lambda n: n["order"])]
    return target, [reading_content(structure, n) for n in structure["nodes"]
                    if n["unit_id"] and within(structure, n, target["id"])]
