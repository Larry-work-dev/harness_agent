from fastapi import APIRouter, Depends

from app.module import db_client as db
from app.module.deps import current_user

router = APIRouter()


@router.get("/memories")
def get_memories(user=Depends(current_user)):
    return db.list_memories(user["id"])

@router.delete("/memories/{mid}")
def remove_memory(mid: int, user=Depends(current_user)):
    db.delete_memory(mid, user["id"])
    return {"ok": True}
