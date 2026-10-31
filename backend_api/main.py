"""Read-only API for the E-O-A outreach application."""

from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException

app = FastAPI(
    title="E-O-A Backend API",
    version="0.2.0",
    description="Read-only campaign information for E-O-A.",
)

CAMPAIGNS_DIR = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "campaigns"
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "eoa-backend-api"}


@app.get("/campaigns")
def get_campaigns():
    """Return safe campaign summaries without exposing sending settings."""

    if not CAMPAIGNS_DIR.is_dir():
        raise HTTPException(
            status_code=500,
            detail="Campaign configuration directory not found.",
        )

    campaigns = []

    try:
        for file_path in sorted(CAMPAIGNS_DIR.glob("*.yaml")):
            with file_path.open("r", encoding="utf-8") as file:
                config = yaml.safe_load(file) or {}

            if not isinstance(config, dict):
                continue

            campaigns.append({
                "id": file_path.stem,
                "name": file_path.stem.replace("_", " "),
                "status": config.get("status", "unspecified"),
                "format": config.get("format", "unspecified"),
            })

    except (OSError, yaml.YAMLError):
        raise HTTPException(
            status_code=500,
            detail="Unable to read campaign configurations.",
        )

    return {
        "count": len(campaigns),
        "campaigns": campaigns,
    }
