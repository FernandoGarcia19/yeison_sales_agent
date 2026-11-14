"""One call to a language model, with its tokens and cost, per business (Y-15). Owned by yeison_migrations."""

from sqlalchemy import BigInteger, Column, DateTime, Integer, Numeric, String
from sqlalchemy.sql import func

from app.models.base import Base


class LlmUsage(Base):
    __tablename__ = "llm_usage"

    id = Column(BigInteger, primary_key=True)
    tenant_id = Column(BigInteger, nullable=False)
    agent_instance_id = Column(BigInteger, nullable=True)
    service = Column(String(20), nullable=False)
    feature = Column(String(60), nullable=False)
    model = Column(String(80), nullable=False)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    cached_tokens = Column(Integer, nullable=False, default=0)
    images = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Numeric(12, 6), nullable=True)
    reference = Column(String(80), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
