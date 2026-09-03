from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import (
    auth,
    categories,
    documents,
    health,
    orgs,
    reconciliation,
    transactions,
)

settings = get_settings()

app = FastAPI(title="Reconcilio API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(orgs.router)
app.include_router(documents.router)
app.include_router(reconciliation.router)
app.include_router(categories.router)
app.include_router(transactions.router)
