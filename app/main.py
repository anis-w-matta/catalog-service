from fastapi import FastAPI

from app.api import customers, items, orders, qra

app = FastAPI(title="Vendo Catalog Service")
app.include_router(items.router)
app.include_router(customers.router)
app.include_router(qra.router)
app.include_router(orders.router)


@app.get("/health")
def health():
    return {"status": "ok"}
