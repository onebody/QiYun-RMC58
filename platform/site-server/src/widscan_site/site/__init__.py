"""Site 服务 — CRUD + bootstrap。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import Site, _utcnow, _uuid


def bootstrap_default_site(session: Session, site_id: str = "site-001",
                           name: str = "WIDSCAN58 Site", timezone: str = "Asia/Shanghai") -> Site:
    """启动时保证至少有一个 Site（V1 只跑一个场地）。"""
    existing = session.get(Site, site_id)
    if existing:
        return existing
    site = Site(id=site_id, name=name, timezone=timezone)
    session.add(site)
    session.commit()
    session.refresh(site)
    return site


def get_site(session: Session, site_id: str) -> Optional[Site]:
    return session.get(Site, site_id)


def list_sites(session: Session) -> list[Site]:
    return session.execute(select(Site).order_by(Site.created_at)).scalars().all()


def upsert_site(session: Session, site_id: str, **kwargs) -> Site:
    site = session.get(Site, site_id)
    if site is None:
        site = Site(id=site_id, **kwargs)
        session.add(site)
    else:
        for k, v in kwargs.items():
            if v is not None:
                setattr(site, k, v)
    session.commit()
    session.refresh(site)
    return site


def site_to_dict(site: Site) -> dict:
    return {
        "id": site.id,
        "name": site.name,
        "description": site.description,
        "location": site.location,
        "timezone": site.timezone,
        "status": site.status,
        "created_at": site.created_at.isoformat() if site.created_at else None,
        "updated_at": site.updated_at.isoformat() if site.updated_at else None,
    }
