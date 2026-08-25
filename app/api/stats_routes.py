from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.challenge_routes import load_challenge
from app.api.dependencies import get_current_user
from app.api.schemas import (
    ActivityDay,
    DashboardOut,
    LeaderboardRow,
    TimeseriesPoint,
    UserOverviewOut,
)
from app.db.database import get_db
from app.db.models import User
from app.services import challenge_service, stats_service

router = APIRouter(tags=["stats"])


@router.get("/challenges/{challenge_uuid}/leaderboard", response_model=list[LeaderboardRow])
def leaderboard(
    challenge_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Standings: wins, targets hit, totals and streaks per participant."""
    challenge, _ = load_challenge(db, challenge_uuid, current_user, require_accepted=False)
    challenge_service.ensure_periods(db, challenge)
    return stats_service.leaderboard(db, challenge)


@router.get("/challenges/{challenge_uuid}/timeseries", response_model=list[TimeseriesPoint])
def timeseries(
    challenge_uuid: str,
    limit: int = Query(default=90, ge=1, le=730),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Target and everyone's value, period by period — for the graphs."""
    challenge, _ = load_challenge(db, challenge_uuid, current_user, require_accepted=False)
    challenge_service.ensure_periods(db, challenge)
    return stats_service.timeseries(db, challenge, limit=limit)


@router.get("/stats/me", response_model=UserOverviewOut)
def my_stats(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """My totals across every challenge."""
    return stats_service.user_overview(db, current_user.id)


@router.get("/stats/dashboard", response_model=DashboardOut)
def dashboard(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """The whole home screen in one request.

    Per active challenge: the period in progress, my number against its target,
    where I sit, my streak, and what everyone else has logged so far.
    """
    return stats_service.dashboard(db, current_user)


@router.get("/stats/me/activity", response_model=list[ActivityDay])
def my_activity(
    days: int = Query(default=90, ge=1, le=730),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Day-by-day totals across every challenge — the heatmap and trend line."""
    return stats_service.activity_history(db, current_user.id, days=days)
