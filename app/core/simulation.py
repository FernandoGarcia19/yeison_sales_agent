"""
Simulation mode for the agent test chat ("Probar agente").

The simulator runs the real pipeline, but inside `activate(run)`:
- every database session joins one transaction that is rolled back at the end of the turn
  (`app.core.database.get_session_factory`), so leads, conversations and sales never persist;
- everything that would leave the service (WhatsApp, Telegram, the panel's stock exit, the bank
  QR service, R2 uploads) is captured in `run.outbox` instead of being sent.

The run lives in a ContextVar, so real traffic handled by the same process is not affected.
"""

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SimulationRun:
    tenant_id: int
    agent_id: int
    session_factory: Any
    customer_phone: str
    outbox: List[Dict[str, Any]] = field(default_factory=list)
    # Bank QR charges of this test conversation: alias -> {estado, tenantId, monto}
    bank_charges: Dict[str, Dict[str, Any]] = field(default_factory=dict)


_current: ContextVar[Optional[SimulationRun]] = ContextVar("simulation_run", default=None)


def current() -> Optional[SimulationRun]:
    return _current.get()


def activate(run: SimulationRun) -> Token:
    return _current.set(run)


def deactivate(token: Token) -> None:
    _current.reset(token)


def capture(channel: str, **data: Any) -> str:
    """Keep what would have been sent; returns a fake message id."""
    run = _current.get()
    if run is None:
        raise RuntimeError("capture() outside a simulation")
    run.outbox.append({"channel": channel, **data})
    return f"sim-{len(run.outbox)}"
