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

    def split(self, title: str, pages: list[str], *, with_offsets=False) -> list[dict]:
        prefix = title[:60] + "\n"
        budget = min(380, 480 - len(self.tokenizer.encode(prefix).ids))
        chunks = []
        for page, text in enumerate(pages, 1):
            position = 0
            while position < len(text):
                # 先限制字符窗口：一个超长单词可能只编码成一个 [UNK] token。
                window = text[position:position + 1000]
                offsets = self.tokenizer.encode(window, add_special_tokens=False).offsets
                if not offsets:
                    position += len(window)
                    continue
                end = min(budget, len(offsets))
                # 尽可能在句末或段末分块，保留少量重叠以承接条款条件。
                if end < len(offsets):
                    for candidate in range(end, end * 2 // 3, -1):
                        boundary = offsets[candidate - 1][1]
                        if (window[boundary - 1:boundary] in "。；！？.!?"
                                or window[boundary:boundary + 1] == "\n"):
                            end = candidate
                            break
                boundary = offsets[end - 1][1]
                content = window[:boundary].strip()
                chunk = {"page": page, "ordinal": len(chunks), "content": content}
                if with_offsets:
                    prefix_text = window[:boundary]
                    chunk.update(start=position + len(prefix_text) - len(prefix_text.lstrip()),
                                 end=position + len(prefix_text.rstrip()))
                chunks.append(chunk)
                if position + boundary >= len(text.rstrip()):
                    break
                position += offsets[max(1, end - 50)][0] if end > 1 else max(1, boundary - 50)
        return chunks

    def passages(self, title, chunks):
        texts = [title[:60] + "\n" + chunk["content"] for chunk in chunks]
        if any(len(self.tokenizer.encode(text).ids) > 512 for text in texts):
            raise ValueError("片段超过嵌入模型输入限制")
        with self.lock:
            return np.asarray(list(self.model.passage_embed(texts, batch_size=16)), dtype=np.float32)

    def query(self, text):
        if len(self.tokenizer.encode(QUERY_PREFIX + text).ids) > 512:
            raise ValueError("搜索内容过长，请缩短为一个具体问题")
        with self.lock:
            return np.asarray(next(self.model.query_embed(QUERY_PREFIX + text)), dtype=np.float32)
