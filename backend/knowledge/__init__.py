"""制度知识库：资料维护、正文检索、结构定位与阅读。"""

import asyncio
import logging

from ..embeddings import LocalEmbeddings
from ..parsing.ocr import LocalOCR
from .library import DuplicateDocument, Library, public_document
from .reading import Reading
from .search import MAX_RESULTS, Search, terms

__all__ = ["MAX_RESULTS", "DuplicateDocument", "KnowledgeBase", "public_document", "terms"]
logger = logging.getLogger(__name__)


class KnowledgeBase(Library, Search, Reading):
    def __init__(self, db, embeddings, ocr=None):
        self.db = db
        self.embeddings = embeddings
        self.ocr = ocr
        self.upload_lock = asyncio.Lock()
        self.term_cache = {}  # 片段 ID → 分词结果；片段内容不变，资料删除或重置时清空

    @classmethod
    async def create(cls, db, settings):
        logger.info("加载本地中文嵌入模型")
        embeddings = await asyncio.to_thread(LocalEmbeddings, settings.model_cache_dir)
        return cls(db, embeddings, LocalOCR(settings.model_cache_dir))
