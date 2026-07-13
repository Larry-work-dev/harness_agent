from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import PROFILES, list_gateway_models
from app.module import db_client as db
from app.module.deps import current_user

router = APIRouter()


class ModelProfileIn(BaseModel):
    name: str
    base_url: str
    model: str
    api_key: str | None = None


@router.get("/models")
def models(user=Depends(current_user)):
    gw = list_gateway_models()
    return {"profiles": PROFILES, "gateway": gw["models"],
            "gateway_error": gw["error"], "custom": db.list_model_profiles(user["id"])}

@router.post("/models/custom")
def add_custom_model(body: ModelProfileIn, user=Depends(current_user)):
    return db.create_model_profile(user["id"], body.name, body.base_url, body.model, body.api_key)

@router.delete("/models/custom/{pid}")
def del_custom_model(pid: int, user=Depends(current_user)):
    db.delete_model_profile(pid, user["id"])
    return {"ok": True}
