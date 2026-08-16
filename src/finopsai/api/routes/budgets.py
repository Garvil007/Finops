"""Budget CRUD."""

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from finopsai.api.deps import get_session
from finopsai.api.schemas import BudgetCreate, BudgetModel, BudgetUpdate
from finopsai.attribution.models import Budget

router = APIRouter(prefix="/api/v1/budgets", tags=["budgets"])


async def _load(session: AsyncSession, budget_id: int) -> Budget:
    """Fetch a budget or raise 404."""
    budget = await session.get(Budget, budget_id)
    if budget is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"budget {budget_id} not found"
        )
    return budget


@router.get("", response_model=list[BudgetModel], summary="List budgets")
async def list_budgets(
    session: Annotated[AsyncSession, Depends(get_session)], team: str | None = None
) -> list[Budget]:
    """Return budgets, optionally narrowed to one team."""
    query = sa.select(Budget).order_by(Budget.team)
    if team:
        query = query.where(Budget.team == team)
    return list(await session.scalars(query))


@router.post(
    "",
    response_model=BudgetModel,
    status_code=status.HTTP_201_CREATED,
    summary="Create a budget",
    description="One budget per team per period; a duplicate is rejected rather than merged.",
)
async def create_budget(
    payload: BudgetCreate, session: Annotated[AsyncSession, Depends(get_session)]
) -> Budget:
    """Create a spend ceiling."""
    existing = await session.scalar(
        sa.select(Budget).where(Budget.team == payload.team, Budget.period == payload.period)
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"a {payload.period} budget already exists for team {payload.team!r}",
        )

    budget = Budget(
        team=payload.team,
        period=payload.period,
        limit_usd=payload.limit_usd,
        alert_thresholds=payload.alert_thresholds,
        is_active=payload.is_active,
    )
    session.add(budget)
    await session.commit()
    await session.refresh(budget)
    return budget


@router.get("/{budget_id}", response_model=BudgetModel, summary="Get one budget")
async def get_budget(
    budget_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> Budget:
    """Return one budget."""
    return await _load(session, budget_id)


@router.patch("/{budget_id}", response_model=BudgetModel, summary="Update a budget")
async def update_budget(
    budget_id: int,
    payload: BudgetUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Budget:
    """Change a budget. Omitted fields keep their stored value."""
    budget = await _load(session, budget_id)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(budget, field, value)

    await session.commit()
    await session.refresh(budget)
    return budget


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a budget")
async def delete_budget(
    budget_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> None:
    """Remove a budget."""
    budget = await _load(session, budget_id)
    await session.delete(budget)
    await session.commit()
