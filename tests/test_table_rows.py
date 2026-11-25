from backend.knowledge.tables import match_rows, render_table_hit


def table(rows):
    return {"columns": ["地区", "城市", "一类", "旺季地区"], "serial": False, "unit": "元/人·天",
            "rows": [{"row": i + 2, "page": 1,
                      "cells": [{"value": v, "bbox": [c * 10, i * 10, c * 10 + 9, i * 10 + 9]}
                                for c, v in enumerate(values)]}
                     for i, values in enumerate(rows)]}


LODGING = table([["北京", "全市", "1100", ""], ["天津", "全市", "800", ""], ["上海", "全市", "1100", ""],
                 ["河北", "石家庄市、张家口市", "800", "张家口市"], ["河北", "石家庄市、张家口市", "800", "承德市"],
                 ["厦门", "全市", "900", ""], ["大连", "全市", "800", "全市"]])


def test_common_words_are_ignored_and_rare_words_pick_rows():
    rows, used, common = match_rows(LODGING, ["全市", "厦门", "住宿费"], 3)
    assert common == ["全市"] and used == ["厦门"]
    assert [fields[0]["value"] for _, fields in rows] == ["厦门"]


def test_ties_prefer_rows_mentioning_the_word_more_often():
    rows, _, _ = match_rows(LODGING, ["张家口"], 3)
    assert [row["row"] for row, _ in rows] == [5, 6]  # 张家口出现两次的行在前


def test_no_rare_match_returns_card_with_row_keys():
    rows, used, _ = match_rows(LODGING, ["住宿费"], 3)
    assert rows == [] and used == []
    hit = {"table_rows": [], "card": "表格：住宿费\n行（共 6 行）：北京 全市、天津 全市", "card_head": "表格：住宿费"}
    assert "行（共 6 行）" in render_table_hit(hit) and "query_table" in render_table_hit(hit)


def test_weak_hits_after_the_first_few_are_cut():
    from backend.knowledge.search import cut_weak
    hits = [{"similarity": v} for v in (.70, .50, .40, .60, .54, .56)]
    assert [h["similarity"] for h in cut_weak(hits)] == [.70, .50, .40, .60, .56]


def test_same_rows_with_different_units_are_not_merged_across_files():
    from backend.knowledge.search import merge_table_copies

    def hit(document, unit):
        return {"id": document, "title": document, "node_id": document, "pages": [1], "citation": "",
                "also_in": [], "signature": f"{unit}|会议类别|住宿费",
                "table_rows": [{"row": 2, "page": 1, "text": "会议类别：业务会议；住宿费：100"}]}

    kept = merge_table_copies([hit("甲", "元/人·天"), hit("乙", "元/间·天"), hit("丙", "元/人•天")])
    # 单位不同的表各自保留；单位写法仅有标点差异的视为同一张表。
    assert [h["id"] for h in kept] == ["甲", "乙"] and kept[0]["also_in"][0]["id"] == "丙"


def test_keyword_top_hits_survive_cut_and_baseline_is_best_similarity():
    from backend.knowledge.search import cut_weak
    hits = [{"similarity": v} for v in (.50, .60, .58, .40, .44)]
    hits[3]["lexical_rank"] = 0  # 关键词第一名：相似度低也保留
    assert [h["similarity"] for h in cut_weak(hits)] == [.50, .60, .58, .40]
    assert cut_weak([]) == []
