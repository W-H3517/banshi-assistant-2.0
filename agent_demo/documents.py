import re
from pathlib import Path


class Documents:
    def __init__(self, directory: Path):
        self.documents = {
            path.stem: path.read_text(encoding="utf-8")
            for path in sorted(directory.glob("*.md"))
        }

    def search(self, query: str) -> list[dict[str, str]]:
        terms = [word for word in re.split(r"[\s，,。；;、]+", query.strip()) if word]
        scored = []
        for document_id, content in self.documents.items():
            score = sum(content.casefold().count(word.casefold()) for word in terms)
            if score:
                scored.append((score, {
                    "document_id": document_id,
                    "title": content.splitlines()[0].lstrip("# "),
                    "summary": content.splitlines()[2],
                }))
        return [document for _, document in sorted(scored, key=lambda item: -item[0])][:5]

    def read(self, document_id: str) -> dict[str, str]:
        if document_id not in self.documents:
            return {"error": "资料 ID 不存在，请先搜索资料。"}
        return {"document_id": document_id, "content": self.documents[document_id]}
