"""FastAPI core API. Intake channels, alerts and the dashboard come later; this slice exposes analyze + results."""

from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI, HTTPException, Request

from .config import Settings
from .memory import Profile
from .models import AnalysisResult, RawMessage
from .pipeline import Pipeline
from .ratelimit import RateLimiter, client_ip
from .runtime import build_pipeline

app = FastAPI(title="ScamShield core API", version="0.1.0")


@lru_cache
def get_pipeline() -> Pipeline:
    return build_pipeline(Settings.from_env())


@lru_cache
def get_limiter() -> RateLimiter:
    s = get_pipeline().settings
    return RateLimiter(s.rate_limit_per_window, s.rate_limit_window_seconds)


@app.get("/health")
def health() -> dict:
    pipeline = get_pipeline()
    return {
        "ok": True,
        "llm": pipeline.llm.name,
        "search": pipeline.search.name,
        "offline": pipeline.llm.offline or pipeline.search.offline,
        "budget_mode": pipeline.budget.mode,
    }


@app.get("/budget")
def budget() -> dict:
    return get_pipeline().budget.status().as_dict()


@app.put("/parents/{parent_id}/profile")
def put_profile(parent_id: str, profile: Profile) -> Profile:
    profile.id = parent_id
    get_pipeline().store.save_profile(profile)
    return profile


@app.post("/parents/{parent_id}/analyze")
def analyze(parent_id: str, raw: RawMessage, request: Request) -> AnalysisResult:
    pipeline = get_pipeline()
    ip = client_ip(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
        trust_forwarded=pipeline.settings.trust_forwarded_for,
    )
    wait = get_limiter().check(ip)
    if wait:
        raise HTTPException(429, "Too many requests, please wait a moment.", headers={"Retry-After": str(int(wait) + 1)})
    return pipeline.analyze(raw, parent_id)


@app.get("/results/{analysis_id}")
def get_result(analysis_id: str) -> AnalysisResult:
    result = get_pipeline().store.get_analysis(analysis_id)
    if result is None:
        raise HTTPException(404, "no such analysis")
    return result
