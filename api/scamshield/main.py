"""FastAPI core API. Intake channels, alerts and the dashboard come later; this slice exposes analyze + results."""

from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI, HTTPException

from .config import Settings
from .memory import Profile
from .models import AnalysisResult, RawMessage
from .pipeline import Pipeline
from .runtime import build_pipeline

app = FastAPI(title="ScamShield core API", version="0.1.0")


@lru_cache
def get_pipeline() -> Pipeline:
    return build_pipeline(Settings.from_env())


@app.get("/health")
def health() -> dict:
    pipeline = get_pipeline()
    return {
        "ok": True,
        "llm": pipeline.llm.name,
        "search": pipeline.search.name,
        "offline": pipeline.llm.offline or pipeline.search.offline,
    }


@app.put("/parents/{parent_id}/profile")
def put_profile(parent_id: str, profile: Profile) -> Profile:
    profile.id = parent_id
    get_pipeline().store.save_profile(profile)
    return profile


@app.post("/parents/{parent_id}/analyze")
def analyze(parent_id: str, raw: RawMessage) -> AnalysisResult:
    return get_pipeline().analyze(raw, parent_id)


@app.get("/results/{analysis_id}")
def get_result(analysis_id: str) -> AnalysisResult:
    result = get_pipeline().store.get_analysis(analysis_id)
    if result is None:
        raise HTTPException(404, "no such analysis")
    return result
