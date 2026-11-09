"""本地 CPU 嵌入；模型下载与正常服务运行分离。"""

import threading

import numpy as np
from fastembed import TextEmbedding
from huggingface_hub import snapshot_download
from tokenizers import Tokenizer

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
MODEL_REPO = "Qdrant/bge-small-zh-v1.5"
MODEL_REVISION = "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


class LocalEmbeddings:
    def __init__(self, cache_dir, *, download=False):
        model_path = snapshot_download(
            repo_id=MODEL_REPO, revision=MODEL_REVISION, cache_dir=str(cache_dir),
            local_files_only=not download,
            allow_patterns=["config.json", "model_optimized.onnx", "special_tokens_map.json",
                            "tokenizer.json", "tokenizer_config.json"],
        )
        self.model = TextEmbedding(
            model_name=MODEL_NAME, cache_dir=str(cache_dir), threads=2,
            specific_model_path=model_path, local_files_only=True,
        )
        # 独立 tokenizer 保留完整 offsets，不修改 FastEmbed 推理时的 tokenizer。
        self.tokenizer = Tokenizer.from_str(self.model.model.tokenizer.to_str())
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.lock = threading.Lock()

    def split(self, context: str, text: str) -> list[dict]:
        """上下文计入输入预算；正常段落完整保留，超长时按句界拆分并保留 offsets。"""
        if len(self.tokenizer.encode(context).ids) > 380 or len(context) > 700:
            raise ValueError("文件及章节信息过长，请缩短文件名或核对标题层级")
        chunks, position = [], 0
        while position < len(text):
            window = text[position:position + 1000 - len(context)]
            offsets = self.tokenizer.encode(window, add_special_tokens=False).offsets
            if not offsets:
                position += len(window)
                continue
            budget = 510 - len(self.tokenizer.encode(context).ids)
            count = min(budget, len(offsets))
            boundary = offsets[count - 1][1]
            while count > 1 and len(self.tokenizer.encode(context + window[:boundary]).ids) > 512:
                count -= 1
                boundary = offsets[count - 1][1]
            if position + boundary < len(text.rstrip()):
                for candidate in range(count, max(1, count // 2), -1):
                    at = offsets[candidate - 1][1]
                    if window[at - 1:at] in "。；！？.!?" or window[at:at + 1] == "\n":
                        boundary = at
                        break
            part = window[:boundary]
            content = part.strip()
            if content:
                chunks.append({"content": content,
                               "start": position + len(part) - len(part.lstrip()),
                               "end": position + len(part.rstrip())})
            position += boundary
        return chunks

    def passages(self, chunks):
        texts = [chunk["retrieval_text"] for chunk in chunks]
        if any(len(self.tokenizer.encode(text).ids) > 512 for text in texts):
            raise ValueError("片段超过嵌入模型输入限制")
        with self.lock:
            return np.asarray(list(self.model.passage_embed(texts, batch_size=16)), dtype=np.float32)

    def query(self, text):
        if len(self.tokenizer.encode(QUERY_PREFIX + text).ids) > 512:
            raise ValueError("搜索内容过长，请缩短为一个具体问题")
        with self.lock:
            return np.asarray(next(self.model.query_embed(QUERY_PREFIX + text)), dtype=np.float32)
