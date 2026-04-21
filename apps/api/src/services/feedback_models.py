"""Feedback DTO — contract feedback-dto.md 에 의거.

- POST /api/feedback request/response
- Admin list item DTO
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class FeedbackRequest(BaseModel):
    """POST /api/feedback body."""

    response_id: UUID
    score: Literal[1, -1]
    comment: Optional[str] = Field(default=None, max_length=2000)


class FeedbackResponse(BaseModel):
    """POST /api/feedback response."""

    id: str
    response_id: str
    score: int
    created_at: datetime
    upserted: bool = False


class AdminFeedbackItem(BaseModel):
    """Admin 리스트 항목 (JOIN api_request_logs)."""

    id: str
    response_id: str
    score: int
    comment: Optional[str] = None
    created_at: datetime
    user_id: str
    profile_id: Optional[str] = None
    faithfulness_score: Optional[float] = None
    question_preview: Optional[str] = None
    answer_preview: Optional[str] = None
    response_ts: Optional[datetime] = None


class AdminFeedbackPage(BaseModel):
    """Admin 리스트 응답."""

    items: list[AdminFeedbackItem]
    total: int
    limit: int
    offset: int
