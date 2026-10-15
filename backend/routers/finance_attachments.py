"""Receipt photos on finance expenses (fin-02): upload, view, delete.

Same rules as the rest of ``/finance``: JWT or personal ``kp_`` token, everything scoped to the
caller, someone else's expense or attachment answers 404 (never 403) -- and ownership is
checked BEFORE the body is read, so a foreign id learns nothing about the file it sent.

The upload endpoint takes the raw ``Request`` instead of a ``UploadFile``: FastAPI would parse
the whole multipart body before the endpoint runs, and the 10 MB cap has to stop the read, not
follow it (see ``services.upload_stream``). The OpenAPI schema is declared by hand for that reason.

NOTE: no ``from __future__ import annotations`` here on purpose -- the upload route is
``@limiter.limit``-decorated (see routers/finance.py).
"""
import uuid

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.ratelimit import ATTACHMENT_READ_LIMIT, ATTACHMENT_UPLOAD_LIMIT, limiter, user_key
from core.storage import StorageBackend, get_storage
from models.finance import FinAttachment
from models.user import User
from routers.finance import finance_user
from schemas.finance import AttachmentOut
from services import attachments as attachments_service
from services import finance as finance_service
from services import upload_stream

router = APIRouter(prefix="/finance", tags=["finance"])

UPLOAD_FIELD = "file"
_UPLOAD_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": [UPLOAD_FIELD],
                    "properties": {UPLOAD_FIELD: {"type": "string", "format": "binary"}},
                }
            }
        },
    }
}


def _attachment_out(a: FinAttachment) -> AttachmentOut:
    return AttachmentOut(
        id=str(a.id),
        mime=a.mime,
        size_bytes=a.size_bytes,
        created_at=a.created_at.isoformat() if a.created_at is not None else None,
        original_name=a.original_name,
    )


@router.post(
    "/expenses/{expense_id}/attachments",
    response_model=AttachmentOut,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=_UPLOAD_BODY,
    responses={
        200: {"description": "The same file was already attached: the existing attachment"},
        409: {"description": "The expense already has the maximum number of attachments"},
        413: {"description": "The file is larger than the limit"},
        415: {"description": "Only JPEG, PNG and WEBP are supported (checked by file signature)"},
    },
)
@limiter.limit(ATTACHMENT_UPLOAD_LIMIT, key_func=user_key)
async def upload_attachment(
    request: Request,
    expense_id: uuid.UUID,
    response: Response,
    current: User = Depends(finance_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> AttachmentOut:
    expense = await finance_service.get_expense(db, current.id, expense_id)  # 404 before any read
    attachments_service.require_encryption_ready()  # 503 in production without FERNET_KEY
    # End the read transaction: a slow client must not hold a pooled connection while up to 10 MB
    # streams in (attributes stay loaded: the session does not expire on commit).
    await db.commit()
    async with attachments_service.upload_slot():  # a few uploads at a time; 503 if none frees up
        upload = await upload_stream.read_single_file(
            request, field=UPLOAD_FIELD, max_bytes=attachments_service.MAX_ATTACHMENT_BYTES
        )
        attachment, created = await attachments_service.add_attachment(
            db, expense, data=upload.data, original_name=upload.filename, storage=storage
        )
    if not created:  # the same picture again: hand back the original, nothing new stored
        response.status_code = status.HTTP_200_OK
    return _attachment_out(attachment)


@router.get(
    "/attachments/{attachment_id}",
    response_class=Response,
    responses={200: {"content": {mime: {} for mime in attachments_service.ATTACHMENT_MIME}}},
)
@limiter.limit(ATTACHMENT_READ_LIMIT, key_func=user_key)
async def download_attachment(
    request: Request,
    attachment_id: uuid.UUID,
    current: User = Depends(finance_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> Response:
    """The picture itself. Type and file name come from the server, never from the client."""
    attachment = await attachments_service.get_attachment(db, current.id, attachment_id)
    data = await attachments_service.read_attachment(attachment, storage)
    extension = attachments_service.extension_for(attachment.mime)
    return Response(
        content=data,
        media_type=attachment.mime,
        headers={
            "X-Content-Type-Options": "nosniff",
            # A receipt is private: no shared or browser cache keeps it after a delete.
            "Cache-Control": "private, no-store",
            # Should the bytes ever be opened as a document, they can load and run nothing.
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Content-Disposition": f'inline; filename="receipt.{extension}"',
        },
    )


@router.delete(
    "/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_attachment(
    attachment_id: uuid.UUID,
    current: User = Depends(finance_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> Response:
    attachment = await attachments_service.get_attachment(db, current.id, attachment_id)
    await attachments_service.delete_attachment(db, attachment, storage)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
