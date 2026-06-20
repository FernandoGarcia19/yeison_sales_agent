"""
Minimal read-only view of the whatsapp_connection table.

The sales agent only needs the Twilio subaccount credentials stored here;
all writes to this table are owned by yeison_panel_backend.
"""

from sqlalchemy import BigInteger, Boolean, Column, String, Text
from app.models.base import Base


class WhatsAppConnection(Base):
    __tablename__ = "whatsapp_connection"

    id = Column(BigInteger, primary_key=True)
    tenant_id = Column(BigInteger, nullable=False)
    agent_instance_id = Column(BigInteger, nullable=False)

    twilio_subaccount_sid = Column(String(255), nullable=True)
    twilio_auth_token_encrypted = Column(Text, nullable=True)
    messaging_service_sid = Column(String(255), nullable=True)

    whatsapp_phone_number = Column(String(50), nullable=True)

    active = Column(Boolean, default=True, nullable=False)
