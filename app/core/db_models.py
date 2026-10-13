from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, JSON
from app.core.database import Base


class EngagementSessionModel(Base):
    __tablename__ = "engagement_session"

    engagement_id = Column(String, primary_key=True, index=True)
    tenant_id = Column(String, nullable=False, index=True)
    state = Column(String, nullable=False, index=True)
    engagement_type = Column(String, nullable=False)
    scope = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)


class CampaignDefinitionModel(Base):
    __tablename__ = "campaign_definition"

    campaign_id = Column(String, primary_key=True, index=True)
    title = Column(String, nullable=False)
    tier = Column(String, nullable=False, index=True)
    playbook_hashes = Column(JSON, nullable=False)
    regions = Column(JSON, nullable=False)
    techniques = Column(JSON, nullable=False)
    credit_cost = Column(Integer, nullable=False)
    content_hash = Column(String, nullable=False, index=True)