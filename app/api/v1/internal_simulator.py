"""
Agent test chat, called by the panel (POST /api/v1/agents/{id}/simulator) with the internal key.
Reached only inside the docker network (nginx returns 404 for /agent/api/v1/internal/ from outside).
"""

import hmac
from typing import Literal, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services import simulator

router = APIRouter(prefix="/internal/simulator", tags=["internal"])


class SimulatorTurn(BaseModel):
    tenant_id: int
    agent_id: int
    session_id: str = Field(..., pattern=r"^[a-f0-9]{8,32}$")
    action: Literal["message", "receipt", "pay", "approve", "reject", "reset"] = "message"
    message: Optional[str] = Field(None, max_length=1000)


@router.post("/turn")
async def simulator_turn(turn: SimulatorTurn, x_internal_key: str = Header(default="")):
    # The panel's INTERNAL_API_KEY, the same key this service uses to call the panel
    if not settings.backend_api_key:
        raise HTTPException(status_code=503, detail="internal key is not configured")
    if not hmac.compare_digest(x_internal_key.encode(), settings.backend_api_key.encode()):
        raise HTTPException(status_code=401, detail="invalid key")
    try:
        return await simulator.run_turn(turn.tenant_id, turn.agent_id, turn.session_id, turn.action, turn.message)
    except simulator.SimulatorError as e:
        raise HTTPException(status_code=e.status, detail=e.message)
