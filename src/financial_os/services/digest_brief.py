"""Optional Grok fiscal brief from structured digest JSON."""

from __future__ import annotations

import json
from typing import Any

import httpx
from sqlalchemy.orm import Session

from financial_os.config import settings
from financial_os.services.digest import build_digest


def build_fiscal_brief(
    session: Session,
    *,
    profile_id: int | None = None,
    scope: str | None = None,
    use_grok: bool = True,
) -> dict[str, Any]:
    dig = build_digest(session, profile_id=profile_id, scope=scope)
    offline = _offline_brief(dig)
    if not use_grok or not settings.grok_enabled:
        return {
            "source": "offline",
            "brief": offline,
            "digest_message": dig.get("message"),
            "alerts": dig.get("alerts", [])[:5],
            "grok_enabled": settings.grok_enabled,
        }

    try:
        prose = _grok_brief(dig)
        return {
            "source": "grok",
            "brief": prose or offline,
            "digest_message": dig.get("message"),
            "alerts": dig.get("alerts", [])[:5],
            "grok_enabled": True,
            "model": settings.xai_model,
        }
    except Exception as e:
        return {
            "source": "offline_fallback",
            "brief": offline,
            "error": str(e)[:200],
            "digest_message": dig.get("message"),
            "alerts": dig.get("alerts", [])[:5],
            "grok_enabled": True,
        }


def _offline_brief(dig: dict[str, Any]) -> str:
    head = dig.get("headline") or {}
    title = head.get("title") or dig.get("message") or "All clear"
    action = (head.get("action") or "").replace("_", " ")
    alerts = dig.get("alerts") or []
    lines = [f"Fiscal focus: {action} — {title}."]
    if alerts:
        lines.append(f"{len(alerts)} alert(s):")
        for a in alerts[:4]:
            lines.append(f"- [{a.get('level')}] {a.get('message')}")
    else:
        lines.append("No critical alerts. Open rarely; keep autopay and buffer intact.")
    ifpp = dig.get("ifpp") or {}
    if ifpp.get("is_red_now"):
        lines.append("RED NOW: protect checking before anything else.")
    elif ifpp.get("next_red_day"):
        lines.append(f"Next red day: {ifpp.get('next_red_day')}.")
    lines.append("Not financial advice — options + reasoning live in capital desk.")
    return "\n".join(lines)


def _grok_brief(dig: dict[str, Any]) -> str | None:
    payload = {
        "task": (
            "Write a short (max 120 words) fiscal briefing for a small business owner. "
            "Prioritize never-negative checking, avoid fees/interest, intentional 0% float OK. "
            "Use only the JSON facts. No investment advice. Plain language."
        ),
        "digest": {
            "message": dig.get("message"),
            "headline": dig.get("headline"),
            "alerts": dig.get("alerts", [])[:8],
            "ifpp": {
                "cash_spendable": (dig.get("ifpp") or {}).get("cash_spendable"),
                "next_red_day": (dig.get("ifpp") or {}).get("next_red_day"),
                "is_red_now": (dig.get("ifpp") or {}).get("is_red_now"),
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.xai_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.xai_model,
        "messages": [
            {"role": "system", "content": "You are a concise liquidity coach. Fiscal soundness first."},
            {"role": "user", "content": json.dumps(payload)},
        ],
        "temperature": 0.3,
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            f"{settings.xai_base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json=body,
        )
        r.raise_for_status()
        data = r.json()
    return data["choices"][0]["message"]["content"].strip()
