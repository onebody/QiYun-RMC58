"""场地归档两端点（S9）：`GET /api/export/site/{site_id}` 读侧、`POST /api/import/site-package` 写侧。

导出挂 `get_session`（汇聚实例也允许导出——它读的就是别人场地的东西）；导入挂
`require_writable_session`（汇聚 405）。ZIP 临时文件用 BackgroundTask 在响应发完后删。
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..deps import get_database, get_session, require_writable_session
from ..services.archive import export_site_archive, import_site_package

router = APIRouter(prefix="/api", tags=["archive"])


@router.get("/export/site/{site_id}")
def export_site(
    site_id: int,
    session: Session = Depends(get_session),
    db=Depends(get_database),
) -> FileResponse:
    tmp, manifest = export_site_archive(session, db, site_id)
    filename = (
        f"rftrack-site-{manifest['site']['slug']}-{manifest['exported_at'][:10]}.zip"
    )
    return FileResponse(
        tmp,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(os.unlink, tmp),
    )


@router.post("/import/site-package")
def import_site_package_endpoint(
    file: UploadFile,
    session: Session = Depends(require_writable_session),
    db=Depends(get_database),
) -> dict:
    payload = file.file.read()
    return import_site_package(db, payload)
