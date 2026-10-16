import os
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 持久缓存（2026-09-28 事故：fastembed 默认缓存 /tmp/fastembed_cache，VM 每次重启即丢，
# 重启后服务尝试联网下载且新版 hub 走 xet CAS（镜像不代理）→ 401 起不来。
# 固化到仓库外挂目录 + 禁 xet 走普通 HTTP（镜像可用）。
os.environ.setdefault("FASTEMBED_CACHE_PATH", os.path.join(os.path.expanduser("~"), "embed-server", "cache"))
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
from fastapi import FastAPI
from pydantic import BaseModel
from fastembed import TextEmbedding

MODEL_NAME = os.environ.get("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
model = TextEmbedding(model_name=MODEL_NAME)

app = FastAPI()

class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str | None = None

@app.post("/v1/embeddings")
def embeddings(req: EmbeddingRequest):
    texts = [req.input] if isinstance(req.input, str) else req.input
    vectors = [v.tolist() for v in model.embed(texts)]
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)],
        "model": req.model or MODEL_NAME,
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }

@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME}
