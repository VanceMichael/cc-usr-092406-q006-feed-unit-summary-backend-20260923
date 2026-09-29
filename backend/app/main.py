from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .database import engine, Base
from .migrations import run_migrations
from .routers import (
    ponds, batches, stocking, feeding, water_quality, medication,
    costs, harvest, analysis, feed_products, reconciliation,
)

Base.metadata.create_all(bind=engine)
# 对既有 SQLite 做幂等列级迁移，并登记历史投喂记录为待换算
run_migrations(engine)

app = FastAPI(
    title="水产养殖管理系统",
    description="支持塘口、批次、投喂计量链（原始单位→生效规格版本→公斤）、周期分析与盘点对账",
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
app.include_router(feed_products.router)
app.include_router(reconciliation.router)
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
