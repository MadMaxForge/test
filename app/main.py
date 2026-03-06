from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(
    title="AI Content Factory",
    description="Automated content factory: Text + Image -> Lip-sync Video",
    version="0.1.0",
)

# Disable CORS. Do not remove this for full-stack development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

from app.routes.generate import router as generate_router
from app.routes.voices import router as voices_router

app.include_router(generate_router)
app.include_router(voices_router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
