from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from llama_index.core import SimpleDirectoryReader, VectorStoreIndex
from llama_index.core.embeddings import resolve_embed_model
from llama_index.llms.ollama import Ollama
from llama_index.readers.web import SimpleWebPageReader
from llama_parse import LlamaParse
import nest_asyncio

# Apply nest_asyncio to fix the event loop issue
nest_asyncio.apply()

# Load environment variables
load_dotenv()

app = FastAPI(title="Animate RAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FILES_DIR = Path(__file__).resolve().parent / "files"
FILES_DIR.mkdir(parents=True, exist_ok=True)

llm = Ollama(model="qwen3.5:4b", request_timeout=120.0, context_window=8192, thinking=False)
parser = LlamaParse(result_type="markdown")
file_extractor = {".docx": parser}
embed_model = resolve_embed_model("local:BAAI/bge-small-en-v1.5")


async def build_index_from_files() -> VectorStoreIndex:
    if not any(p.is_file() for p in FILES_DIR.iterdir()):
        return VectorStoreIndex.from_documents([], embed_model=embed_model)
    reader = SimpleDirectoryReader(str(FILES_DIR), file_extractor=file_extractor)
    documents = await reader.aload_data()
    return VectorStoreIndex.from_documents(documents, embed_model=embed_model)


@app.on_event("startup")
async def startup_event():
    app.state.vector_index = await build_index_from_files()


async def query_index(index: VectorStoreIndex, prompt: str) -> str:
    query_engine = index.as_query_engine(llm=llm)
    result = await query_engine.aquery(prompt)
    return str(result)


async def query_web(url: str, prompt: str) -> str:
    documents = SimpleWebPageReader(html_to_text=True).load_data([url])
    web_index = VectorStoreIndex.from_documents(documents, embed_model=embed_model)
    return await query_index(web_index, prompt)


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), filename: str = Form(...)):
    safe_name = Path(filename).name
    if not safe_name:
        raise HTTPException(status_code=400, detail="Filename is required")

    # Optional: restrict file types
    allowed_exts = {".pdf", ".docx", ".txt", ".md"}
    if Path(safe_name).suffix.lower() not in allowed_exts:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {safe_name}")

    file_path = FILES_DIR / safe_name
    contents = await file.read()
    file_path.write_bytes(contents)

    # Rebuild index after upload
    app.state.vector_index = await build_index_from_files()

    return JSONResponse(content={"message": f"File '{safe_name}' uploaded successfully"})


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
