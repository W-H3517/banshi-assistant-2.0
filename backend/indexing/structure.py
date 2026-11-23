"""结构树与阅读单元：唯一一处根据段落推导文件、章节、条款、段落、表格及表格行的层级。

节点只描述层级和来源页；正文保存在阅读单元中，一个节点至多一个单元。
节点 ID 带本次解析代次，重新解析后旧 ID 失效。
"""

from uuid import uuid4

from ..parsing.markers import article_label, heading_level
from .table_text import grid_content, row_content

TABLE_WARNING = "表格字段、单位及续页表头为解析结果，请核对原文"
LOW_CONFIDENCE = "含低分识别文字，请对照原文"
# 效力段、附件目录结束此前全部层级；通知结语在没有正式条款时结束编号项。
END_ALL = ("附件：", "附件:")
END_ITEMS = ("本通知", "特此通知")


def source_path(by_id, node, body_heading_id=None):
    """节点所属的章节路径与条目路径，如 ("第一章总则 / （一）申请材料", "第一条 / 1.凭证")。"""
    chapters, articles = [], []
    while node["parent_id"]:
        node = by_id[node["parent_id"]]
        if node["id"] == body_heading_id:
            continue
        if node["kind"] == "chapter":
            chapters.append(node["label"])
        elif node["kind"] == "article":
            articles.append(node["label"])
    return " / ".join(reversed(chapters)), " / ".join(reversed(articles))


def build_structure(sections, tables, title, filename):
    generation = uuid4().hex
    nodes, units, by_id = [], [], {}
    tables = {table["id"]: table for table in tables}
    table_nodes = {}

    def add(kind, label, parent=None, pages=()):
        node = {"id": f"{generation}:{len(nodes)}", "kind": kind, "label": label,
                "parent_id": parent, "order": len(nodes), "pages": list(pages),
                "unit_id": None, "length": 0, "warnings": [],
                "boundary_status": "confirmed", "boundary_reasons": []}
        nodes.append(node)
        by_id[node["id"]] = node
        return node

    def attach(node, kind, content, spans, **extra):
        unit = {"id": f"{generation}:u{len(units)}", "node_id": node["id"], "kind": kind,
                "content": content, "spans": spans, **extra}
        units.append(unit)
        node["unit_id"], node["length"] = unit["id"], len(content)
        return unit

    def inherit_boundary(node, section):
        node["boundary_status"] = section["boundary_status"]
        node["boundary_reasons"] = section["boundary_reasons"]

    root = add("document", title)
    headings, formal, item = {}, None, None  # 标题栈（层级→节点）、当前正式条款、当前编号项

    def heading_parent():
        return headings[max(headings)]["id"] if headings else root["id"]

    def structural_parent():
        # 正式条款内的“一、”“（一）”小标题挂在条款下；篇章节始终在条款之上。
        if formal and (not headings or max(headings) < 3):
            return formal["id"]
        return heading_parent()

    def add_table(section, parent):
        table = tables[section["table_id"]]
        node = table_nodes.get(table["id"])
        if node is None:
            label = table["title"] or source_path(by_id, {"parent_id": parent})[0] or section["title"]
            node = table_nodes[table["id"]] = add(
                "table", label, parent, sorted({f["page"] for f in table["fragments"]}))
            content, spans = grid_content(table, label)
            attach(node, "table", content, spans, table_ref={"table": table["id"]})
            node["warnings"] = [TABLE_WARNING]
        for row in table["rows"]:
            if row["section_id"] != section["id"]:
                continue
            child = add("table_row", f"第 {row['row']} 行", node["id"], [row["page"]])
            content = row_content(table, row, node["label"])
            attach(child, "table_row", content,
                   [{"start": 0, "end": len(content), "page": row["page"]}],
                   table_ref={"table": table["id"], "row": row["row"]})
            child["warnings"] = [TABLE_WARNING]
            inherit_boundary(child, section)

    pending = None  # 上一段若是独立标题，其阅读单元只留给紧随其后的这一段
    for section in sections:
        heading, pending = pending, None
        kind, content = section["kind"], section["content"].strip()
        if kind == "document_title":
            continue
        if kind == "effect" or content.startswith(END_ALL):
            headings, formal, item = {}, None, None
        elif not formal and content.startswith(END_ITEMS):
            headings, item = {}, None
        if kind == "table":
            add_table(section, item["id"] if item else structural_parent())
            continue
        spans = section["source_spans"]
        level = heading_level(content)
        if level is not None:
            headings = {key: node for key, node in headings.items() if key < level}
            if level < 3:
                formal = None
            item = None
            node = add("chapter", content, structural_parent(), section["pages"])
            pending = attach(node, "chapter", section["content"], spans)
            headings[level] = node
            continue
        label = article_label(content)
        if label.startswith("第"):
            headings = {key: node for key, node in headings.items() if key < 3}
            formal = add("article", label, heading_parent(), section["pages"])
            item = None
        elif label:
            item = add("article", label, structural_parent(), section["pages"])
        if label and content == label:
            # 只有条号或编号的一行：正文在随后的段落里，本行只作条目标题。
            pending = attach(nodes[-1], "article", section["content"], spans)
            continue
        parent_id = item["id"] if item else structural_parent()
        node = add("paragraph", section["title"], parent_id, section["pages"])
        body = section["content"]
        extra = {}
        if "caption_of" in section:
            extra["caption_of"] = section["caption_of"]
        elif label:
            # 编号与正文同在原文块内，所属标题已经在正文开头。
            extra["body_heading_id"] = parent_id
        elif kind == "text" and heading and heading["node_id"] == parent_id:
            # 独立标题只交给紧随其后、直接挂在其下的段落，保留原文坐标，不跨表格或结构边界拼接。
            parent = by_id[parent_id]
            parent["unit_id"], parent["length"] = None, 0
            prefix = heading["content"] + "\n"
            body = prefix + body
            spans = heading["spans"] + [
                {**span, "start": span["start"] + len(prefix), "end": span["end"] + len(prefix)}
                for span in spans
            ]
            node["pages"] = sorted(set(node["pages"]) | {span["page"] for span in heading["spans"]})
            extra["body_heading_id"] = parent_id
        attach(node, "article_body" if kind == "article" else "paragraph", body, spans, **extra)
        inherit_boundary(node, section)
        if any(s.get("confidence") is not None and s["confidence"] < .9 for s in spans):
            node["warnings"] = [LOW_CONFIDENCE]

    # 自底向上汇总：结构节点的页码覆盖全部子节点，边界不确定向上传递。
    for node in reversed(nodes[1:]):
        parent = by_id[node["parent_id"]]
        parent["pages"] = sorted(set(parent["pages"]) | set(node["pages"]))
        if node["boundary_status"] != "confirmed":
            parent["boundary_status"] = node["boundary_status"]
            parent["boundary_reasons"] = list(dict.fromkeys(
                parent["boundary_reasons"] + node["boundary_reasons"]))
    units = [unit for unit in units if by_id[unit["node_id"]]["unit_id"] == unit["id"]]
    return {"generation": generation, "filename": filename, "nodes": nodes, "units": units}
