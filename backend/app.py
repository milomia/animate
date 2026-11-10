import asyncio
import logging
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from llama_index.core import VectorStoreIndex
from llama_index.core.embeddings import resolve_embed_model
from llama_index.llms.ollama import Ollama
from starlette.concurrency import run_in_threadpool
import nest_asyncio

from ingestion import IngestionError, SUPPORTED_EXTENSIONS, chunk_document, ingest_bytes, ingest_web_content
from ingestion.nodes import to_nodes
from ingestion.web import fetch_url

# Apply nest_asyncio to fix the event loop issue
nest_asyncio.apply()

# Load environment variables
load_dotenv()

logger = logging.getLogger("aimate")

app = FastAPI(title="Animate RAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FILES_DIR = Path(__file__).resolve().parent / "files"
FILES_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

llm = Ollama(model="qwen3.5:4b", request_timeout=120.0, context_window=8192, thinking=False)


@lru_cache(maxsize=1)
def get_embed_model():
    # Loaded on first use, not at import, so importing the app stays cheap and tests can substitute it.
    return resolve_embed_model("local:BAAI/bge-small-en-v1.5")


def build_index(nodes) -> VectorStoreIndex:
    if not nodes:
        return VectorStoreIndex.from_documents([], embed_model=get_embed_model())
    return VectorStoreIndex(nodes=nodes, embed_model=get_embed_model())


def nodes_from_bytes(name: str, data: bytes):
    """Parse one file locally into retrieval nodes. Returns (nodes, warnings)."""
    parsed = ingest_bytes(name, data)
    return to_nodes(chunk_document(parsed)), parsed.warnings


def replace_nodes(index: VectorStoreIndex, old_ids, new_nodes) -> None:
    # Insert before deleting: if embedding the new nodes fails, the file's previous version stays searchable.
    index.insert_nodes(new_nodes)
    if old_ids:
        index.delete_nodes(old_ids, delete_from_docstore=True)


@app.on_event("startup")
async def startup_event():
    """Parse every stored file once and build the index from the results. A file that cannot be read is
    logged and skipped instead of stopping the app."""
    app.state.index_lock = asyncio.Lock()
    app.state.node_ids = {}
    all_nodes = []
    for path in sorted(FILES_DIR.iterdir()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            nodes, warnings = await run_in_threadpool(nodes_from_bytes, path.name, path.read_bytes())
        except IngestionError as exc:
            logger.warning("Skipping %s: %s", path.name, exc)
            continue
        for warning in warnings:
            logger.warning("%s: %s", path.name, warning)
        app.state.node_ids[path.name] = [node.node_id for node in nodes]
        all_nodes.extend(nodes)
    app.state.vector_index = await run_in_threadpool(build_index, all_nodes)


async def query_index(index: VectorStoreIndex, prompt: str) -> str:
    query_engine = index.as_query_engine(llm=llm)
    result = await query_engine.aquery(prompt)
    return str(result)


def nodes_from_url(url: str):
    data, kind = fetch_url(url)
    parsed = ingest_web_content(url, data, kind)
    return to_nodes(chunk_document(parsed)), parsed.warnings


async def query_web(url: str, prompt: str) -> str:
    nodes, _ = await run_in_threadpool(nodes_from_url, url)
    if not nodes:
        raise IngestionError("No readable text was found at that URL.")
    web_index = await run_in_threadpool(build_index, nodes)
    return await query_index(web_index, prompt)


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), filename: str = Form(...)):
    safe_name = Path(filename).name
    if not safe_name:
        raise HTTPException(status_code=400, detail="Filename is required")

    if Path(safe_name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {safe_name}")

    contents = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")

    # Parse before saving, so a corrupt or unreadable upload is rejected and never stored.
    try:
        nodes, warnings = await run_in_threadpool(nodes_from_bytes, safe_name, contents)
    except IngestionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if not nodes:
        raise HTTPException(status_code=422, detail=" ".join(["No text could be extracted from the file."] + warnings))

    (FILES_DIR / safe_name).write_bytes(contents)
    async with app.state.index_lock:
        await run_in_threadpool(replace_nodes, app.state.vector_index, app.state.node_ids.get(safe_name, []), nodes)
        app.state.node_ids[safe_name] = [node.node_id for node in nodes]

    return JSONResponse(content={
        "message": f"File '{safe_name}' uploaded successfully",
        "chunks": len(nodes),
        "warnings": warnings,
    })


def to_http_error(exc: Exception, prefix: str) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int) or not 400 <= status < 600:
        status = 500
    return HTTPException(status_code=status, detail=f"{prefix}: {type(exc).__name__}: {exc}")


@app.post("/api/query_llm")
async def process_query(query: str = Form(...)):
    try:
        result = await query_index(app.state.vector_index, query)
        return JSONResponse(content={"result": result})
    except Exception as exc:
        raise to_http_error(exc, "Query failed") from exc


@app.post("/api/query_web")
async def process_web_query(url: str = Form(...), query: str = Form(...)):
    try:
        result = await query_web(url, query)
        return JSONResponse(content={"result": result})
    except Exception as exc:
        raise to_http_error(exc, "Web query failed") from exc


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
