from copy import deepcopy
from io import BytesIO

from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

from backend.document_preview import convert_pdf
from backend.reference_preview import extract_edges, trace_references
from backend.table_preview import describe_tables


def document(identifier, title, texts):
    return {"id": identifier, "title": title, "page_count": len(texts), "sections": [
        {"id": i, "title": heading, "kind": "article", "content": text,
         "pages": [i]} for i, (heading, text) in enumerate(texts, 1)
    ]}


def test_bidirectional_refs_do_not_confuse_same_article_numbers():
    a = document("a", "差旅办法", [
        ("第一条", "第一条 住宿费标准按本办法第二条执行。"),
        ("第二条", "第二条 住宿费标准与申请材料要求。"),
        ("第三条", "第三条 住宿费标准另有规定的除外，申请材料须补充。"),
    ])
    b = document("b", "培训办法", [
        ("第一条", "第一条 培训不适用《差旅办法》第2条。"),
        ("第二条", "第二条 本办法第一条规定特殊情形。"),
    ])
    result = trace_references([a, b], "a", 2)
    assert {(e["source"]["document_id"], e["source"]["section_id"])
            for e in result["incoming"]} == {("a", 1), ("b", 1)}
    assert result["exception_candidates"][0]["target"]["section_id"] == 3
    edge = trace_references([a, b], "b", 2)["outgoing"][0]
    assert edge["targets"][0]["document_id"] == "b"
    assert edge["targets"][0]["section_id"] == 1


def test_missing_and_ambiguous_versions_are_not_selected():
    source = document("s", "培训制度", [("第一条", "第一条 依据《差旅办法》第二条和《未入库办法》第三条。")])
    v1 = document("v1", "差旅办法", [("第二条", "第二条 旧标准。")])
    v2 = document("v2", "差旅办法", [("第二条", "第二条 新标准。")])
    result = trace_references([source, v1, v2], "s", 1)
    assert [e["status"] for e in result["outgoing"]] == ["ambiguous", "missing"]
    assert len(result["outgoing"][0]["targets"]) == 2
    assert result["outgoing"][1]["targets"] == []


def test_unknown_external_names_do_not_bind_to_local_articles():
    doc = document("a", "差旅办法", [
        ("第一条", "第一条 按采购办法第二条执行。"),
        ("第二条", "第二条 本地同号条款。"),
    ])
    edge = trace_references([doc], "a", 1)["outgoing"][0]
    assert edge["status"] == "missing"
    assert edge["targets"] == []


def test_reference_scope_can_switch_back_to_local_in_same_sentence():
    a = document("a", "差旅办法", [
        ("第一条", "第一条 根据《培训办法》第二条及本办法第三条、第四条办理。"),
        ("第三条", "第三条 本地规定。"), ("第四条", "第四条 本地补充。"),
    ])
    b = document("b", "培训办法", [("第二条", "第二条 培训规定。"),
                                  ("第四条", "第四条 不应命中。")])
    edges = trace_references([a, b], "a", 1)["outgoing"]
    assert [e["targets"][0]["document_id"] for e in edges] == ["b", "a", "a"]


def test_exception_candidates_ignore_article_numbers_and_include_host_conditions():
    doc = document("a", "差旅办法", [
        ("第二十一条", "第二十一条 住宿费按住宿标准限额报销。"),
        ("第二十三条", "第二十三条 特殊业务按照采购程序处理。"),
        ("第二十条", "第二十条 举办方提供住宿的，凭住宿费承担证明办理。"),
    ])
    result = trace_references([doc], "a", 1)
    assert [c["target"]["section_id"] for c in result["exception_candidates"]] == [3]


def test_annex_target_includes_content_and_all_source_pages():
    doc = document("a", "办法", [("第一条", "第一条 按附件2执行。")])
    doc["sections"].extend([
        {"id": 2, "title": "附件2", "kind": "heading", "pages": [2], "content": "附件2"},
        {"id": 3, "title": "表", "kind": "table", "pages": [3], "content": "具体标准表"},
    ])
    target = trace_references([doc], "a", 1)["outgoing"][0]["targets"][0]
    assert target["pages"] == [2, 3]
    assert "具体标准表" in target["content"]


def test_document_only_refs_aliases_and_unresolved_pronouns():
    source = document("s", "培训制度", [
        ("第一条", "第一条 《差旅管理办法》（以下简称“差旅办法”）。"),
        ("第二条", "第二条 按差旅办法第十二条执行。"),
        ("第三条", "第三条 上述办法第十二条不适用。"),
    ])
    target = document("t", "差旅管理办法", [("第十二条", "第十二条 住宿标准。")])
    edges = extract_edges([source, target])
    assert edges[0]["scope"] == "document"
    assert edges[1]["targets"][0]["document_id"] == "t"
    assert edges[2]["status"] == "missing"
    assert len(trace_references([source, target], "t", 1)["incoming"]) == 2


def test_merged_cells_expand_only_with_actual_geometry():
    stream = BytesIO()
    table = Table([["Region", "Category", "Limit"], ["City A", "A", "500"],
                   ["", "B", "400"], ["", "C", "300"]], colWidths=[90, 90, 90])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black),
                               ("SPAN", (0, 1), (0, 2))]))
    SimpleDocTemplate(stream).build([table])
    converted = convert_pdf(stream.getvalue())
    result = describe_tables(converted["sections"], "Example")
    rows = result[0]["rows"]
    assert rows[0]["fields"][0]["value"] == "City A"
    assert rows[1]["fields"][0]["value"] == "City A"
    assert rows[1]["fields"][0]["source_row"] == 2
    assert rows[2]["fields"][0]["value"] == ""
    assert "空白（未推断）" in rows[2]["content"]

    # 相邻续页只能候选继承表头，不继承上页的地区数据。
    section = converted["sections"][0]
    continuation = deepcopy(section)
    continuation["id"] = 2
    continuation["pages"] = [2]
    continuation["table_data"]["grid"] = [continuation["table_data"]["grid"][-1]]
    tables = describe_tables([section, continuation], "Example")
    assert tables[1]["header_pages"] == [1]
    assert tables[1]["rows"][0]["fields"][0]["value"] == ""
    assert tables[1]["needs_review"] is True
