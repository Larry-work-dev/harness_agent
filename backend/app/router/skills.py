from fastapi import APIRouter

from app.module import mcp_client

router = APIRouter()


@router.get("/skills")
def list_skills():
    return mcp_client.list_tools_metadata()
