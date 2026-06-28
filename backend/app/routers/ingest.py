"""
Write/ingest endpoints. Batch upload requires a bearer token (see
app/security.py) -- only trusted internal jobs or operators should be
pushing EOD files into the pipeline.
"""
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.ingestion.batch_loader import ingest_eod_file
from app.ingestion.websocket_feed import manager
from app.rbac import CurrentUser, Role, require_role
from app.schemas import IngestBatchResult
from app.security import rate_limit_dependency

logger = logging.getLogger("fmda.routers.ingest")

router = APIRouter(prefix="/api/ingest", tags=["ingest"])
ws_router = APIRouter(tags=["stream"])


@router.post(
    "/batch",
    response_model=IngestBatchResult,
    dependencies=[Depends(rate_limit_dependency)],
)
async def upload_eod_batch(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_role(Role.ADMIN, Role.SERVICE)),
    db: AsyncSession = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith((".csv", ".json")):
        raise HTTPException(400, "Only .csv or .json files are accepted")

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:  # 50MB guardrail
        raise HTTPException(413, "File too large (max 50MB)")

    try:
        stats = await ingest_eod_file(db, filename=file.filename, content=content, actor=current_user.username)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    logger.info("batch ingest by %s: %s -> %s", current_user.username, file.filename, stats)
    return IngestBatchResult(
        rows_received=stats.received,
        rows_inserted=stats.inserted,
        rows_updated=stats.updated,
        rows_deduped=stats.deduped,
        status="success",
    )


@ws_router.websocket("/ws/live")
async def live_feed(websocket: WebSocket):
    """Browsers connect here to receive normalized ticks as they're ingested."""
    await manager.connect(websocket)
    try:
        while True:
            # We don't expect inbound messages, but awaiting receive keeps
            # the connection alive and lets us detect disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
