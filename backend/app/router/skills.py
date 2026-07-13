from fastapi import APIRouter

from app.module.skills import load_skills

router = APIRouter()


@router.get("/skills")
def list_skills():
    return [{"name": s.name, "description": s.description,
             "when_to_use": s.when_to_use, "parameters": s.parameters} for s in load_skills()]
