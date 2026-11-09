"""首次准备本地嵌入模型：uv run --locked python -m backend.prepare。"""

from .config import Settings
from .embeddings import MODEL_NAME, LocalEmbeddings
from .ocr import model_paths


def main():
    settings = Settings.load()
    print(f"准备本地模型 {MODEL_NAME}，缓存目录：{settings.model_cache_dir}", flush=True)
    embeddings = LocalEmbeddings(settings.model_cache_dir, download=True)
    result = embeddings.query("出差住宿报销限额")
    print(f"模型准备完成，本地 CPU 推理通过，向量维数：{len(result)}", flush=True)
    print("准备 RapidOCR 本地识别模型", flush=True)
    model_paths(settings.model_cache_dir, download=True)
    print("OCR 模型准备完成，文件校验通过", flush=True)


if __name__ == "__main__":
    main()
