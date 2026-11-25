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
