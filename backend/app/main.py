from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .database import init_db
from .routers import (
    ponds, batches, stocking, feeding, water_quality, medication,
    costs, harvest, analysis, specs, reviews, recalc,
)

init_db()

app = FastAPI(
    title="水产养殖管理系统",
    description="支持塘口、批次、投喂计量链（版本化包装规格/复核隔离/分批回算）、周期分析与追溯",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ponds.router)
app.include_router(batches.router)
app.include_router(stocking.router)
app.include_router(feeding.router)
app.include_router(specs.router)
app.include_router(reviews.router)
app.include_router(recalc.router)
app.include_router(water_quality.router)
app.include_router(medication.router)
app.include_router(costs.router)
app.include_router(harvest.router)
app.include_router(analysis.router)

@app.get("/")
def root():
    return {
        "message": "欢迎使用水产养殖管理系统API",
        "docs": "/docs",
        "version": "2.0.0"
    }

@app.get("/health")
def health_check():
    return {"status": "healthy"}
