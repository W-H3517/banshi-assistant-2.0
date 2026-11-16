"""结构定位与确定性阅读：按节点原文顺序返回，不按相关性截取。"""

import json

from sqlalchemy import select

from ..db import Chunk, DocumentIndex
from ..indexing.table_text import UNIT_UNKNOWN, row_fields
from .outline import (
    STALE,
    decode_cursor,
    encode_cursor,
    find_outline,
    load_outlines,
    normalize_label,
)

READ_BUDGET = 6000            # 一次返回的正文字符数
READ_RESPONSE_BUDGET = 24000  # 一次返回的 JSON 总字符数，含来源与边界说明
LOCATE_LIMIT = 30
VERIFICATION_PAGES = 12
SCOPES = ("node", "article", "neighbors", "page")
# 定位只匹配结构名称；正文段落和表格行不参与。
LOCATABLE = ("document", "chapter", "article", "table")


def table_view(outline, table, node, ref, include_geometry):
    """表格来源说明：默认只给行标识与表头页，坐标明细按需返回。"""
    view = {"id": f"{outline.generation}:{table['id']}", "unit": table["unit"] or UNIT_UNKNOWN,
            "header_pages": table["header_pages"]}
    hint = {"target_id": node["id"], "include_geometry": True}
    if "row" not in ref:
        view.update(columns=table["columns"], row_count=len(table["rows"]))
        if include_geometry:
            view.update(header_cells=table["header_cells"], unit_sources=table["unit_sources"])
        else:
            view["geometry_hint"] = hint
        return view, set()
    row = next(r for r in table["rows"] if r["row"] == ref["row"])
    fields = row_fields(table, row)
    inherited = [f for f in fields if "inherited_from" in f]
    view.update(row=row["row"], row_key=[f["value"] for f in fields[:3]],
                continued=row["continued"])
    if include_geometry:
        view.update(fields=fields, header_paths=table["columns"],
                    header_cells=table["header_cells"], unit_sources=table["unit_sources"])
    else:
        view["geometry_hint"] = hint
        view["inherited_fields"] = [{"name": f["name"], "value": f["value"],
                                     "from_page": f["inherited_from"]["page"]} for f in inherited]
    return view, {f["inherited_from"]["page"] for f in inherited}


def unit_parts(outline, nodes, units, tables, include_geometry):
    parts = []
    for node in nodes:
        unit = units[node["unit_id"]]
        part = {"node": node, "content": unit["content"], "spans": unit["spans"],
                "unit_id": unit["id"], "table": None, "header_pages": [], "extra_pages": set(),
                "boundary_status": node["boundary_status"],
                "boundary_reasons": node["boundary_reasons"], "warnings": node["warnings"]}
        if "table_ref" in unit:
            table = tables[unit["table_ref"]["table"]]
            part["table"], part["extra_pages"] = table_view(
                outline, table, node, unit["table_ref"], include_geometry)
            part["header_pages"] = table["header_pages"]
        parts.append(part)
    return parts


def page_parts(outline, target, blocks, page):
    """原页核验：逐块返回该页全部文字，包括被排除的页脚、脚注和落款。"""
    return [{"node": {"id": f"{target['id']}@{block['id']}", "parent_id": outline.root["id"],
                      "kind": block["kind"], "label": f"原页块 {block['id']}", "pages": [page]},
             "content": block["text"], "spans": [{"start": 0, "end": len(block["text"]), "page": page}],
             "unit_id": None, "table": None, "header_pages": [], "extra_pages": set(),
             "boundary_status": "uncertain", "boundary_reasons": ["原页核验不等于整条内容"],
             "warnings": [block["excluded_reason"]] if "excluded_reason" in block else []}
            for block in blocks]


class Reading:
    async def locate(self, query="", document_id=None, parent_id=None, cursor=None):
        if len(query) > 300:
            raise ValueError("定位内容不能超过 300 字")
        outlines = await load_outlines(self.db, [document_id] if document_id else None)
        if document_id and not outlines:
            raise ValueError("制度不存在，请重新定位")
        if parent_id:
            owner = next((o for o in outlines.values()
                          if parent_id.startswith(o.generation + ":")), None)
            if owner is None:
                raise ValueError(STALE)
            parent_id = owner.node(parent_id)["id"]
        scope = ["locate", query, document_id, parent_id,
                 sorted(outline.generation for outline in outlines.values())]
        offset = decode_cursor(cursor, scope)
        normalized = normalize_label(query)
        tokens = [normalize_label(word) for word in query.split()]
        results = []
        for outline in outlines.values():
            for node in outline.nodes:
                if parent_id and not outline.within(node, parent_id):
                    continue
                if not query.strip():
                    # 不带关键词：列出文件，或列出指定节点的直接子节点。
                    listed = node["parent_id"] == parent_id if parent_id else node["kind"] == "document"
                    if not listed:
                        continue
                else:
                    # 有重名时保留全部候选，由路径区分。
                    if node["kind"] not in LOCATABLE:
                        continue
                    label = normalize_label(node["label"])
                    if node["kind"] == "document":
                        label += normalize_label(outline.meta["filename"])
                    if normalized not in label and not all(t in label for t in tokens):
                        continue
                results.append(outline.info(node))
        if offset > len(results):
            raise ValueError("定位游标超出范围，请重新定位")
        end = offset + LOCATE_LIMIT
        return {"items": results[offset:end], "complete": end >= len(results),
                "next_cursor": encode_cursor(scope, end) if end < len(results) else None,
                "total": len(results)}

    async def read(self, target_id, scope="node", neighbors=1, cursor=None, page=None,
                   include_geometry=False):
        if scope not in SCOPES or not 0 <= neighbors <= 3:
            raise ValueError("阅读范围须为 node/article/neighbors/page，相邻段数为 0 到 3")
        async with self.db.sessions() as session:
            # 片段 ID 先换成它所属的节点。
            node_id = await session.scalar(select(Chunk.node_id).where(Chunk.id == target_id))
        outline = await find_outline(self.db, node_id or target_id)
        target = outline.node(node_id or target_id)
        if scope == "page":
            if page is None or not 1 <= page <= len(outline.meta["pages"]):
                raise ValueError("核验原页须指定有效的PDF物理页码")
        elif page is not None:
            raise ValueError("page参数仅用于原页核验scope=page")
        columns = [DocumentIndex.blocks] if scope == "page" else [DocumentIndex.units,
                                                                DocumentIndex.tables]
        async with self.db.sessions() as session:
            loaded = (await session.execute(select(*columns).where(
                DocumentIndex.document_id == outline.document_id))).one()
        if scope == "page":
            parts = page_parts(outline, target, loaded.blocks[page - 1], page)
        else:
            target, nodes = outline.select(target, scope, neighbors)
            parts = unit_parts(outline, nodes, {u["id"]: u for u in loaded.units},
                               {t["id"]: t for t in loaded.tables}, include_geometry)

        cursor_scope = ["read", outline.generation, target_id, scope, neighbors, page,
                        include_geometry]
        offset = decode_cursor(cursor, cursor_scope)
        total = sum(len(part["content"]) for part in parts)
        if offset > total:
            raise ValueError("续读游标超出内容范围，请重新读取")
        remaining, position, items, extra_pages = READ_BUDGET, 0, [], set()
        for part in parts:
            text = part["content"]
            start = max(0, offset - position)
            position += len(text)
            if start >= len(text):
                continue
            end = min(len(text), start + remaining)
            pages = sorted({s["page"] for s in part["spans"] if s["start"] < end and s["end"] > start})
            item = {**outline.info(part["node"]), "content": text[start:end], "start": start,
                    "end": end, "pages": pages, "page": pages[0],
                    "citation": outline.citation(pages[0]),
                    "excerpt_complete": start == 0 and end == len(text),
                    "unit_id": part["unit_id"], "boundary_status": part["boundary_status"],
                    "boundary_reasons": part["boundary_reasons"], "table": part["table"],
                    "header_pages": part["header_pages"],
                    "warnings": outline.warnings(pages) + part["warnings"]}
            # 给外层页码、游标和边界说明留预算；几何信息默认不进入模型上下文。
            if len(json.dumps(items + [item], ensure_ascii=False)) > READ_RESPONSE_BUDGET - 5000:
                if items:
                    break
                raise ValueError("单项来源信息超过返回预算，请不带几何信息读取并核对原PDF")
            items.append(item)
            extra_pages |= part["extra_pages"]
            remaining -= end - start
            if remaining == 0:
                break
        end_offset = offset + READ_BUDGET - remaining
        confirmed = bool(parts) and all(p["boundary_status"] == "confirmed" for p in parts)
        reasons = list(dict.fromkeys(r for p in parts for r in p["boundary_reasons"]))
        if not parts:
            reasons.append("该结构节点没有可读取正文，需核对原页")
        source_pages = sorted({p for item in items for p in item["pages"] + item["header_pages"]}
                              | extra_pages) or target["pages"][:1]
        checked = source_pages[:VERIFICATION_PAGES]
        return {"target": outline.info(target), "items": items,
                "pagination_complete": end_offset >= total,
                "boundary_status": "confirmed" if confirmed else "uncertain",
                "reading_complete": end_offset >= total and confirmed,
                "missing_or_uncertain_parts": reasons,
                "verification_pages": [{"page": p, "citation": outline.citation(p)} for p in checked],
                "verification_pages_complete": len(source_pages) <= VERIFICATION_PAGES,
                "verification_hint": {
                    "target_id": outline.root["id"], "scope": "page",
                    "pages": sorted({p for origin in checked for p in (origin, origin + 1)
                                     if p <= len(outline.meta["pages"])})},
                "next_cursor": encode_cursor(cursor_scope, end_offset) if end_offset < total else None,
                "note": "分页结束仅表示本次范围读完；边界confirmed基于显式结构，不保证OCR/表格语义无误或覆盖远处例外。"}
