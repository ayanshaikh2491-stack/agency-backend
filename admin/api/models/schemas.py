"""Pydantic schemas for the TAGS Agency API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Chat ───────────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    """Incoming chat message from the frontend."""

    message: str = Field(..., description="The user's message")
    conversation_id: str | None = Field(
        None, description="Optional conversation ID for continuity"
    )
    workspace_id: str | None = Field(
        None, description="Target workspace (None = agency-level)"
    )
    agent_type: str | None = Field(
        None,
        description=(
            "Which agent to route to. "
            "Only valid inside a workspace: sba, seo, content, website, analytics. "
            "Omit for the Agency CEO."
        ),
    )


class ChatResponse(BaseModel):
    """Response from an agent chat."""

    response: str = Field(..., description="The agent's response text")
    conversation_id: str | None = Field(None, description="Continuation token")
    agent_type: str | None = Field(None, description="Which agent responded")
    thinking_phases: list[dict[str, Any]] | None = Field(
        None, description="Multi-phase thinking trace (CEO only)"
    )


# ── Workspace CRUD ─────────────────────────────────────────────────────────


class ClientContext(BaseModel):
    """Client business context — used by all agents to understand the client.

    Content Agent uses this to know:
    - What business → what kind of visuals to create
    - Website URL → auto-discover brand colors, logo, style
    - Industry → what style/tone works
    - Target audience → what appeals to them
    - Social links → where to post, platform-specific sizes
    """

    website_url: str | None = Field(
        None, description="Client website URL (used for brand auto-discovery)"
    )
    industry: str | None = Field(
        None, description="Business industry: real_estate, saas, ecommerce, healthcare, food, education, etc."
    )
    target_audience: str | None = Field(
        None, description="Who the client sells to: young_professionals, homeowners, etc."
    )
    brand_colors: list[str] = Field(
        default_factory=list, description="Brand hex colors if known: ['#FF6B35', '#004E89']"
    )
    brand_style: str | None = Field(
        None, description="Visual style: modern, minimal, bold, playful, corporate, luxury"
    )
    social_links: dict[str, str] = Field(
        default_factory=dict, description="Social media links: instagram, facebook, twitter, linkedin, youtube"
    )
    competitors: list[str] = Field(
        default_factory=list, description="Competitor website URLs (for gap analysis)"
    )
    description: str | None = Field(
        None, description="Brief description of what the client does"
    )


class WorkspaceCreate(BaseModel):
    """Payload to create a new client workspace."""

    name: str = Field(..., description="Human-readable workspace name")
    client_name: str | None = Field(None, description="Client or brand name")
    description: str = Field("", description="Purpose / context")
    client_context: ClientContext | None = Field(
        None,
        description=(
            "Client business context — website, industry, audience, brand colors. "
            "All agents use this to understand the client. "
            "If website_url is provided, brand colors/style auto-discovered."
        ),
    )


class WorkspaceOut(BaseModel):
    """Serialised workspace."""

    id: str
    name: str
    client_name: str | None
    description: str
    created_at: datetime
    agents: list[str] = Field(default_factory=list)
    client_context: ClientContext | None = None


# ── Health ─────────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    ceo_ready: bool = False
    workspace_count: int = 0
