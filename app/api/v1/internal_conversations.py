"""
The panel's inbox (Y-69): pause the agent in a conversation, answer as a person and give it back.
Called by the panel (POST /api/v1/conversations/{id}/{pause,resume,reply}) with the internal key; reached only
inside the docker network (nginx returns 404 for /agent/api/v1/internal/ from outside). The logic is in
`app/services/inbox.py`.
"""

import hmac
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services import inbox

router = APIRouter(prefix="/internal/conversations", tags=["internal"])


def _verify_internal_key(x_internal_key: str = Header(default="")) -> None:
    # The panel's INTERNAL_API_KEY, the same key this service uses to call the panel
    if not settings.backend_api_key:
        raise HTTPException(status_code=503, detail="internal key is not configured")
    if not hmac.compare_digest(x_internal_key.encode(), settings.backend_api_key.encode()):
        raise HTTPException(status_code=401, detail="invalid key")


class Scope(BaseModel):
    tenant_id: int


class Reply(Scope):
    text: str = Field(..., min_length=1, max_length=4000)
    author: Optional[str] = Field(None, max_length=120)


async def _run(call):
    try:
        return await call
    except inbox.InboxError as e:
        raise HTTPException(status_code=e.status, detail=e.message)


@router.post("/{conversation_id}/pause", dependencies=[Depends(_verify_internal_key)])
async def pause(conversation_id: int, body: Scope):
    return await _run(inbox.pause_conversation(conversation_id, body.tenant_id))


@router.post("/{conversation_id}/resume", dependencies=[Depends(_verify_internal_key)])
async def resume(conversation_id: int, body: Scope):
    return await _run(inbox.resume_conversation(conversation_id, body.tenant_id))


@router.post("/{conversation_id}/reply", dependencies=[Depends(_verify_internal_key)])
async def reply(conversation_id: int, body: Reply):
    return await _run(inbox.reply(conversation_id, body.tenant_id, body.text, body.author))
