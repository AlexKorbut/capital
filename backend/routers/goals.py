"""Goals router (Feature #5) — savings / FIRE targets with progress."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.deps import get_current_user
from models.user import User
from schemas.goal import GoalCreate, GoalOut
from services import goals as goals_service

router = APIRouter(prefix="/goals", tags=["goals"])


@router.get("", response_model=list[GoalOut])
async def list_goals(
    current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[GoalOut]:
    rows = await goals_service.list_goals_with_progress(db, current.id)
    return [GoalOut.model_validate(r) for r in rows]


@router.post("", response_model=GoalOut, status_code=status.HTTP_201_CREATED)
async def create_goal(
    body: GoalCreate,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GoalOut:
    await goals_service.create_goal(
        db, current.id, body.title, body.target_usd, body.target_date
    )
    rows = await goals_service.list_goals_with_progress(db, current.id)
    # Return the newly created goal (last by created_at).
    return GoalOut.model_validate(rows[-1])


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_goal(
    goal_id: uuid.UUID,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    ok = await goals_service.delete_goal(db, current.id, goal_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Цель не найдена")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
