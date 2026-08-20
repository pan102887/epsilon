from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

router = APIRouter(tags=["test"])

# Resolve and create the resource asset directory relative to the project root.
BASE_DIR = Path(__file__).resolve().parents[4]
STATIC_DIR = BASE_DIR / "resource"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
router.mount("/resource", StaticFiles(directory=STATIC_DIR), name="resource")


@router.get("/favicon.ico")
async def favicon() -> FileResponse:
    icon_path = STATIC_DIR / "images" / "icon.jpg"
    return FileResponse(icon_path, media_type="image/jpeg")


@router.get("/api/test/get")
async def test_get() -> dict[str, str]:
    return {"message": "hello fastapi!!"}
