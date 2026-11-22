"""
轻量级 RAG 引擎（框架无关，适配 2核 4G 云服务器）
-------------------------------------------------
- PDF 解析：PyMuPDF (fitz)，按页提取文本并保留页码 metadata
- 向量化：外部 Embedding API（硅基流动 BAAI/bge-m3，OpenAI 兼容格式）
- 向量库：ChromaDB 本地持久化（零外部数据库依赖）
- 问答生成：外部 LLM API（DeepSeek，OpenAI 兼容格式）
"""

import os
from pathlib import Path

import chromadb
import fitz  # PyMuPDF
from openai import OpenAI

DATA_DIR = Path(__file__).parent / "data"
PDF_DIR = DATA_DIR / "pdfs"
CHROMA_DIR = DATA_DIR / "chroma"
PDF_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_DIR.mkdir(parents=True, exist_ok=True)


class RagError(Exception):
    """统一业务异常，携带 HTTP 状态码与可读提示"""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


_chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
_collection = _chroma_client.get_or_create_collection(
    name="research_papers",
    metadata={"hnsw:space": "cosine"},
)


def _get_embed_client() -> tuple[OpenAI, str]:
    api_key = os.getenv("EMBED_API_KEY", "").strip()
    base_url = os.getenv("EMBED_BASE_URL", "https://api.siliconflow.cn/v1").strip()
    model = os.getenv("EMBED_MODEL", "BAAI/bge-m3").strip()
    if not api_key or api_key.startswith("sk-你的"):
        raise RagError(500, "后端未配置有效的 EMBED_API_KEY（请检查 .env）")
    return OpenAI(api_key=api_key, base_url=base_url), model


def _get_llm_client() -> tuple[OpenAI, str]:
    api_key = os.getenv("LLM_API_KEY", "").strip()
    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").strip()
    model = os.getenv("LLM_MODEL", "deepseek-chat").strip()
    if not api_key or api_key.startswith("sk-你的"):
        raise RagError(500, "后端未配置有效的 LLM_API_KEY（请检查 .env）")
    return OpenAI(api_key=api_key, base_url=base_url), model


def _embed_texts(texts: list[str]) -> list[list[float]]:
    client, model = _get_embed_client()
    vectors: list[list[float]] = []
    batch_size = 16
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = client.embeddings.create(model=model, input=batch)
        vectors.extend([item.embedding for item in resp.data])
    return vectors


def _chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    text = " ".join(text.split())
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def list_pdf_files() -> list[dict]:
    files = []
    for p in sorted(PDF_DIR.glob("*.pdf")):
        files.append({"id": p.name, "name": p.name, "size_kb": round(p.stat().st_size / 1024, 1)})
    return files


def index_pdf(filename: str, content: bytes) -> dict:
    safe_name = Path(filename).name
    if not safe_name.lower().endswith(".pdf"):
        raise RagError(400, "目前仅支持上传 .pdf 文件")

    pdf_path = PDF_DIR / safe_name
    pdf_path.write_bytes(content)

    delete_pdf_vectors(safe_name)

    doc = fitz.open(pdf_path)
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []

    for page_idx, page in enumerate(doc, start=1):
        page_text = page.get_text("text") or ""
        chunks = _chunk_text(page_text)
        for c_idx, chunk in enumerate(chunks):
            ids.append(f"{safe_name}::p{page_idx}::c{c_idx}")
            documents.append(chunk)
            metadatas.append({"file_name": safe_name, "page": page_idx})
    doc.close()

    if not documents:
        raise RagError(400, "未能从该 PDF 中提取出文本（可能是纯扫描图片）")

    embeddings = _embed_texts(documents)
    _collection.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)

    return {"id": safe_name, "name": safe_name, "chunks": len(documents)}


def delete_pdf_vectors(filename: str) -> None:
    safe_name = Path(filename).name
    _collection.delete(where={"file_name": safe_name})


def delete_pdf(filename: str) -> bool:
    safe_name = Path(filename).name
    pdf_path = PDF_DIR / safe_name
    delete_pdf_vectors(safe_name)
    if pdf_path.exists():
        pdf_path.unlink()
        return True
    return False


def rag_chat(question: str, file_ids: list[str] | None, base_url_for_files: str, top_k: int = 5) -> dict:
    if _collection.count() == 0:
        raise RagError(400, "知识库为空，请先让管理员上传至少一篇 PDF 论文")

    query_vec = _embed_texts([question])[0]

    where_filter = None
    if file_ids:
        where_filter = (
            {"file_name": file_ids[0]}
            if len(file_ids) == 1
            else {"file_name": {"$in": file_ids}}
        )

    n_results = min(top_k, _collection.count())
    results = _collection.query(
        query_embeddings=[query_vec],
        n_results=n_results,
        where=where_filter,
        include=["documents", "metadatas", "distances"],
    )

    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]

    if not docs:
        return {"answer": "在选定的论文中没有检索到相关内容，请尝试换个问法或勾选更多论文。", "citations": []}

    context_blocks = []
    citations = []
    for idx, (doc_text, meta, dist) in enumerate(zip(docs, metas, dists), start=1):
        fname = meta.get("file_name", "unknown.pdf")
        page = meta.get("page", 1)
        score = max(0.0, min(1.0, 1.0 - float(dist)))
        context_blocks.append(f"[{idx}] 来源：{fname}（第 {page} 页）\n{doc_text}")
        citations.append(
            {
                "index": idx,
                "file_name": fname,
                "page": page,
                "score": round(score, 3),
                "snippet": doc_text[:360] + ("..." if len(doc_text) > 360 else ""),
                "pdf_url": f"{base_url_for_files.rstrip('/')}/files/{fname}#page={page}",
            }
        )

    system_prompt = (
        "你是一位严谨的科研论文助手。请严格根据下面提供的论文片段回答用户问题，"
        "使用中文作答，并在关键结论后用 [1]、[2] 标明引用来源编号。"
        "如果提供的片段中没有足够信息，请明确说明，不要编造。"
    )
    user_prompt = "参考论文片段：\n\n" + "\n\n---\n\n".join(context_blocks) + f"\n\n用户问题：{question}"

    llm_client, llm_model = _get_llm_client()
    completion = llm_client.chat.completions.create(
        model=llm_model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    answer = completion.choices[0].message.content or ""

    return {"answer": answer, "citations": citations}
