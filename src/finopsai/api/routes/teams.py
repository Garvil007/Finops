"""Team CRUD."""

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from finopsai.api.deps import get_session
from finopsai.api.schemas import TeamCreate, TeamModel
from finopsai.attribution.models import Team

router = APIRouter(prefix="/api/v1/teams", tags=["teams"])


@router.get("", response_model=list[TeamModel], summary="List registered teams")
async def list_teams(session: Annotated[AsyncSession, Depends(get_session)]) -> list[Team]:
    """Return every registered cost owner."""
    return list(await session.scalars(sa.select(Team).order_by(Team.name)))


@router.post(
    "",
    response_model=TeamModel,
    status_code=status.HTTP_201_CREATED,
    summary="Register a team",
)
async def create_team(
    payload: TeamCreate, session: Annotated[AsyncSession, Depends(get_session)]
) -> Team:
    """Register a cost owner."""
    if await session.get(Team, payload.name) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"team {payload.name!r} already exists",
        )

    team = Team(
        name=payload.name,
        display_name=payload.display_name,
        slack_channel=payload.slack_channel,
    )
    session.add(team)
    await session.commit()
    await session.refresh(team)
    return team


@router.get("/{name}", response_model=TeamModel, summary="Get one team")
async def get_team(name: str, session: Annotated[AsyncSession, Depends(get_session)]) -> Team:
    """Return one registered cost owner."""
    team = await session.get(Team, name)
    if team is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"team {name!r} not found"
        )
    return team


@router.delete(
    "/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a team",
    description="Cost records keep their team label; only the registration is removed.",
)
async def delete_team(name: str, session: Annotated[AsyncSession, Depends(get_session)]) -> None:
    """Remove a team registration."""
    team = await session.get(Team, name)
    if team is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"team {name!r} not found"
        )
    await session.delete(team)
    await session.commit()
