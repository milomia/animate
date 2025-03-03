from fastapi import FastAPI, File, UploadFile, Form
from llama_index.llms.ollama import Ollama
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from llama_parse import LlamaParse
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader
from llama_index.readers.web import SimpleWebPageReader
from fastapi import FastAPI, File, UploadFile, Form
from llama_index.core.embeddings import resolve_embed_model
from dotenv import load_dotenv
import nest_asyncio

# Apply nest_asyncio to fix the event loop issue
nest_asyncio.apply()


# Load environment variables
load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize LLM and other components
llm = Ollama(model="mistral", request_timeout=30.0)
parser = LlamaParse(result_type="markdown")
file_extractor = {".docx": parser}
embed_model = resolve_embed_model("local:BAAI/bge-m3")
documents = SimpleDirectoryReader("./files", file_extractor=file_extractor).load_data()
vector_index = VectorStoreIndex.from_documents(documents, embed_model=embed_model)

async def query_web(url: str, prompt: str):
    documents = SimpleWebPageReader(html_to_text=True).load_data([url])
    vector_index = VectorStoreIndex.from_documents(documents, embed_model=embed_model)
    query_engine = vector_index.as_query_engine(llm=llm)
    result = await query_engine.aquery(prompt)
    return result

async def query_llm(prompt: str):
    # documents = SimpleDirectoryReader("./files", file_extractor=file_extractor).load_data()
    # vector_index = VectorStoreIndex.from_documents(documents, embed_model=embed_model)
    query_engine = vector_index.as_query_engine(llm=llm)
    result = await query_engine.aquery(prompt)
    return result

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), filename: str = Form(...)):
    file_location = f"files/{filename}"
    with open(file_location, "wb") as f:
        f.write(await file.read())
    return JSONResponse(content={"message": f"File '{filename}' uploaded successfully"})

@app.post("/api/query_llm")
async def process_query(query: str = Form(...)):
    result = await query_llm(query)
    print("result=",result)
    return JSONResponse(content={"result": str(result)})

@app.post("/api/query_web")
async def process_query(url: str = Form(...), query=Form(...)):
    result = await query_web(url, query)
    print("result=",result)
    return JSONResponse(content={"result": str(result)})

if __name__ == "__main__":
    print("hello mike")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
