"""Small FastAPI service for inventory items."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()
ITEMS = {}


class Item(BaseModel):
    name: str
    quantity: int


@app.post("/items")
def create_item(item: Item):
    ITEMS[item.name] = item.quantity
    return {"name": item.name, "quantity": item.quantity}


@app.get("/items/{name}")
def get_item(name: str):
    try:
        return {"name": name, "quantity": ITEMS[name]}
    except KeyError:
        raise HTTPException(status_code=404, detail="not found") from None


@app.delete("/items/{name}")
def delete_item(name):
    try:
        del ITEMS[name]
        return {"deleted": name}
    except Exception as exc:
        return {"error": str(exc)}
