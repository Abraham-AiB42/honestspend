"""Smoke path for release confidence (temp data dir)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from financial_os.config import settings
from financial_os.db import init_db, make_engine, make_session_factory
from financial_os.seed import seed_all

td = Path(tempfile.mkdtemp(prefix="lr-smoke-"))
object.__setattr__(settings, "data_dir", td)
object.__setattr__(settings, "host", "127.0.0.1")
object.__setattr__(settings, "require_api_key", False)
object.__setattr__(settings, "allow_non_loopback", False)

import financial_os.api.app as app_mod

app_mod.engine = make_engine()
app_mod.SessionLocal = make_session_factory(app_mod.engine)
init_db(app_mod.engine)
with app_mod.SessionLocal() as s:
    seed_all(s)
    s.commit()

c = TestClient(app_mod.app)


def must(label: str, r, code: int = 200):
    assert r.status_code == code, f"{label}: {r.status_code} {r.text[:200]}"
    print(f"OK {label}")
    if r.headers.get("content-type", "").startswith("application/json"):
        return r.json()
    return {}


must("health", c.get("/api/health"))
profiles = must("profiles", c.get("/api/profiles"))
assert any(p["slug"] == "personal" for p in profiles)
# Seed must not include legacy private business slugs
legacy = {p["slug"] for p in profiles}
assert "personal" in legacy
assert len(legacy) == 1  # personal only on fresh seed

must(
    "add business",
    c.post("/api/profiles", json={"display_name": "Smoke Biz", "entity_type": "business"}),
)
must(
    "add child",
    c.post(
        "/api/profiles",
        json={
            "display_name": "Smoke Child",
            "entity_type": "child",
            "parent_profile_id": profiles[0]["id"],
        },
    ),
)

must(
    "quick-setup",
    c.post(
        "/api/onboarding/quick-setup",
        json={
            "cash_balance": 5000,
            "card_name": "Card",
            "card_limit": 3000,
            "card_due_day": 15,
        },
    ),
)
ifpp = must("ifpp", c.get("/api/ifpp", params={"scope": "entity"}))
assert "cash_spendable" in ifpp
must("buy", c.post("/api/pre-purchase", json={"amount": 50}))
must("rescue", c.post("/api/liquidity/rescue", json={"amount": 100}))
must(
    "simulate",
    c.post(
        "/api/ifpp/simulate",
        json={
            "extra_outflows": [
                {"amount": 100, "name": "test", "on_date": "2026-08-20"}
            ]
        },
    ),
)
bak = must("backup", c.post("/api/backup/create", json={"as_zip": True, "note": "smoke"}))
assert bak.get("name")
must("restore stage", c.post(f"/api/backup/restore/{bak['name']}"))
must("fee candidates", c.get("/api/fees/candidates"))
must("transfers", c.get("/api/transfers/candidates"))
must("brief", c.get("/api/digest/brief", params={"use_grok": False}))
must("autopay", c.get("/api/autopay"))
must("graph", c.get("/api/intermix/graph"))
must("glance", c.get("/api/glance"))
must("home simple", c.get("/api/home/simple"))
# first-run on empty would conflict with quick-setup already run; just check route exists via health path
must("payments", c.get("/api/payments/candidates"))
r = c.get("/glance")
assert r.status_code == 200 and b"Safe to spend" in r.content
print("OK glance page")
print("SMOKE OK", td)
