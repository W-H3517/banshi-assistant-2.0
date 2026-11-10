"""从明确原文建立文件规则卡；不推断文件效力或补写缺失标准。"""

import re


def build_rule_card(structure):
    rules = []
    nodes = {n["id"]: n for n in structure["nodes"]}
    for unit in structure["reading_units"]:
        if unit["kind"] in ("table_row", "chapter"):
            continue
        for match in re.finditer(r"[^\n]+", unit["content"]):
            text = match[0]
            kinds = []
            if re.search(r"不一致.*为准|同时废止|予以废止|本(?:通知|办法|规定|细则).*(?:施行|实施)", text):
                kinds.append("effect")
            if re.search(r"适用(?:于|范围)|因公临时出国[（(]境[）)].*(?:执行|办法)", text):
                kinds.append("scope")
            if re.search(r"(?:执行|参照|按照).*(?:《[^》]+(?:办法|规定)[^》]*》|因公临时出国.*办法)", text):
                kinds.append("reference")
            if not kinds:
                continue
            node = nodes[unit["node_id"]]
            path = []
            ancestor = node
            while ancestor["parent_id"]:
                ancestor = nodes[ancestor["parent_id"]]
                path.append(ancestor["label"])
            pages = sorted({s["page"] for s in unit["spans"]
                            if s["start"] < match.end() and s["end"] > match.start()}) or node["pages"]
            references = re.findall(r"《([^》]+)》", text) if "reference" in kinds else []
            if "reference" in kinds and not references:
                references = re.findall(r"参照(.+?办法)", text)
            rules.append({"kinds": kinds, "content": text, "node_id": node["id"],
                          "unit_id": unit["id"], "start": match.start(), "end": match.end(),
                          "pages": pages, "boundary_status": node["boundary_status"],
                          "scope_path": list(reversed(path)),
                          "references": references})
    return {"rules": rules, "note": "仅摘录明确原文，局部规则按章节路径理解，不扩为全文件规则；空卡不表示无限制。外部文件指向不提供其金额标准。"}
