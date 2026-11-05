from agent_demo.config import ROOT
from agent_demo.documents import Documents


def test_search_and_read_are_grounded_and_paths_are_not_accepted():
    documents = Documents(ROOT / "data" / "fixtures")
    results = documents.search("住宿 培训")
    assert {item["document_id"] for item in results} == {"travel", "training"}
    assert "450" in documents.read("travel")["content"]
    assert documents.search("火星补贴") == []
    assert "error" in documents.read("../../.env")
