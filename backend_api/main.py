"""Initial E-O-A API service. This deliberately exposes health only for now.
Operational endpoints will be added after authentication and engine integration
are designed and tested; do not put email credentials in this file.
"""
import os
from fastapi import FastAPI

app = FastAPI(
    title="E-O-A Backend API",
    version="0.1.0",
    description="Backend API for the E-O-A outreach application.",
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "eoa-backend-api"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
    )
