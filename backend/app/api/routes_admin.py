"""Admin routes — invite management and user listing.

All endpoints require `get_admin_athlete`. Non-admins get a 403.
"""
from __future__ import annotations

import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_athlete, get_db
from app.config import get_settings
from app.models.schema import Athlete, Invite

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Invite code: 10 URL-safe chars — ~60 bits of entropy. Plenty for short-lived codes.
_INVITE_CODE_BYTES = 8


def _generate_code() -> str:
    return secrets.token_urlsafe(_INVITE_CODE_BYTES)[:10]


def _invite_url(code: str) -> str:
    frontend = get_settings().frontend_origin.split(",")[0].strip()
    return f"{frontend}/signup?{urlencode({'invite': code})}"


class CreateInviteRequest(BaseModel):
    email_hint: str | None = Field(
        default=None,
        description="Optional free-text hint (e.g. recipient's email) for your own records.",
    )
    expires_in_days: int | None = Field(
        default=14,
        ge=1,
        le=365,
        description="Days until the invite expires. Null = never expires.",
    )


class InviteOut(BaseModel):
    code: str
    email_hint: str | None
    used_by_athlete_id: int | None
    created_at: int
    used_at: int | None
    expires_at: int | None
    invite_url: str


class UserOut(BaseModel):
    id: int
    firstname: str | None
    lastname: str | None
    username: str | None
    is_admin: bool
    created_at: int | None


def _invite_to_out(inv: Invite) -> InviteOut:
    return InviteOut(
        code=inv.code,
        email_hint=inv.email_hint,
        used_by_athlete_id=inv.used_by_athlete_id,
        created_at=inv.created_at,
        used_at=inv.used_at,
        expires_at=inv.expires_at,
        invite_url=_invite_url(inv.code),
    )


@router.post("/invites", response_model=InviteOut)
async def create_invite(
    body: CreateInviteRequest,
    admin: Athlete = Depends(get_admin_athlete),
    db: AsyncSession = Depends(get_db),
):
    now = int(time.time())
    # Retry on the extremely unlikely collision.
    for _ in range(5):
        code = _generate_code()
        exists = (
            await db.execute(select(Invite).where(Invite.code == code))
        ).scalar_one_or_none()
        if exists is None:
            break
    else:
        raise HTTPException(status_code=500, detail="Could not generate unique invite code")

    expires_at = (
        now + body.expires_in_days * 86400 if body.expires_in_days else None
    )
    inv = Invite(
        code=code,
        email_hint=body.email_hint,
        created_by_athlete_id=admin.id,
        created_at=now,
        expires_at=expires_at,
    )
    db.add(inv)
    await db.commit()
    return _invite_to_out(inv)


@router.get("/invites", response_model=list[InviteOut])
async def list_invites(
    include_used: bool = False,
    admin: Athlete = Depends(get_admin_athlete),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Invite).order_by(desc(Invite.created_at))
    if not include_used:
        stmt = stmt.where(Invite.used_by_athlete_id.is_(None))
    rows = (await db.execute(stmt)).scalars().all()
    return [_invite_to_out(r) for r in rows]


@router.delete("/invites/{code}")
async def revoke_invite(
    code: str,
    admin: Athlete = Depends(get_admin_athlete),
    db: AsyncSession = Depends(get_db),
):
    inv = (
        await db.execute(select(Invite).where(Invite.code == code))
    ).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if inv.used_by_athlete_id is not None:
        raise HTTPException(status_code=400, detail="Cannot revoke a used invite")
    await db.delete(inv)
    await db.commit()
    return {"ok": True}


@router.get("/users", response_model=list[UserOut])
async def list_users(
    admin: Athlete = Depends(get_admin_athlete),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(select(Athlete).order_by(desc(Athlete.created_at)))
    ).scalars().all()
    return [
        UserOut(
            id=r.id,
            firstname=r.firstname,
            lastname=r.lastname,
            username=r.username,
            is_admin=r.is_admin,
            created_at=r.created_at,
        )
        for r in rows
    ]
