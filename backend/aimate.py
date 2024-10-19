from fastapi import FastAPI

app = FastAPI()

@app.get("/api/upload/{file_name}")
async def download_file(file_name: str):
    file_path = f"/path/to/your/files/{file_name}"
    
    return FileResponse(file_path, filename=file_name)

