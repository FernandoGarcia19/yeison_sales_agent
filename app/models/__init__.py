"""
Database models for the WhatsApp AI Sales Agent.

These models map to the existing database schema managed by the main backend.
This service primarily reads data for workflow execution.
"""

from app.models.tenant import Tenant
from app.models.agent_instance import AgentInstance
from app.models.inventory import InventoryTenant
from app.models.lead import Lead
from app.models.conversation import SalesConversation

__all__ = [
    "Tenant",
    "AgentInstance",
    "InventoryTenant",
    "Lead",
    "SalesConversation",
]
