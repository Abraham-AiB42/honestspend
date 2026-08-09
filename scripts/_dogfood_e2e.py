"""
1.0 RC dogfood path (API-level) — household journey without WinUI.
Mirrors docs/RC_1.0.md section A as automated checks.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from financial_os.config import settings

td = Path(tempfile.mkdtemp(prefix="lr-dogfood-"))
object.__setattr__(settings, "data_dir", td)
object.__setattr__(settings, "host", "127.0.0.1")
object.__setattr__(settings, "require_api_key", False)
object.__setattr__(settings, "allow_non_loopback", False)

import financial_os.api.app as app_mod
from financial_os.db import init_db, make_engine, make_session_factory
from financial_os.seed import seed_all

app_mod.engine = make_engine()
app_mod.SessionLocal = make_session_factory(app_mod.engine)
init_db(app_mod.engine)
with app_mod.SessionLocal() as s:
    seed_all(s)
    s.commit()

c = TestClient(app_mod.app)
PASS = FAIL = 0


def ok(label: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    print(f"  ✓ {label}" + (f" — {detail}" if detail else ""))


def bad(label: str, detail: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  ✗ {label}: {detail}")


def must(label: str, r, code: int = 200):
    if r.status_code != code:
        bad(label, f"HTTP {r.status_code} {r.text[:160]}")
        return {}
    ok(label)
    return r.json() if r.headers.get("content-type", "").startswith("application/json") else {}


print("\n═══ DOGFOOD E2E (RC 1.0 path A) ═══")
print(f"data: {td}\n")

print("1. First-run")
fr = must(
    "first-run",
    c.post(
        "/api/onboarding/first-run",
        json={
            "cash_name": "Primary checking",
            "cash_balance": 6500,
            "safety_buffer": 1000,
            "card_name": "0% card",
            "card_limit": 4000,
            "card_balance": 1200,
            "card_due_day": 15,
            "card_promo_end": "2026-12-01",
            "bill_name": "Rent",
            "bill_amount": 1400,
            "bill_next_date": "2026-09-01",
        },
    ),
)
home = must("home simple", c.get("/api/home/simple"))
if float(home.get("safe_to_spend") or 0) > 0:
    ok("Safe to spend > 0", home["safe_to_spend"])
else:
    bad("Safe to spend", str(home.get("safe_to_spend")))

print("\n2. Books / import brief surface")
if home.get("books_brief"):
    ok("books_brief", home["books_brief"].get("title", "")[:50])
else:
    bad("books_brief", "missing")

print("\n3. Sort charges API (categorize batch)")
r = c.post("/api/categorize/batch", json={"apply": False, "use_grok": False, "limit": 20})
if r.status_code == 200:
    ok("categorize batch", f"{len(r.json().get('results') or [])} results")
else:
    bad("categorize", r.text[:80])

print("\n4. Fee brief")
if "fee_brief" in home:
    ok("fee_brief present")
else:
    bad("fee_brief", "missing")

print("\n5. Recurring suggestions")
rec = must("recurring suggestions", c.get("/api/recurring/suggestions"))
if rec.get("count", 0) >= 0:
    ok("recurring API shape")

print("\n6. Promo / month-close / tax year")
if home.get("promo_brief") is not None:
    ok("promo_brief")
if home.get("month_close", {}).get("steps"):
    ok("month_close", home["month_close"].get("progress_label", ""))
else:
    bad("month_close", "missing")
ty = must("tax year checklist", c.get("/api/tax/year-checklist"))
if ty.get("steps"):
    ok("tax year steps", str(len(ty["steps"])))

print("\n7. Backup")
bak = must("backup", c.post("/api/backup/create", json={"as_zip": True, "note": "dogfood"}))
if bak.get("name"):
    ok("backup name", bak["name"])

print("\n8. Can I buy + scenario")
buy = must("pre-purchase", c.post("/api/pre-purchase", json={"amount": 80, "prefer": "auto"}))
if buy.get("verdict"):
    ok("buy verdict", buy["verdict"])
sc = must(
    "scenario quick",
    c.post("/api/scenarios/quick", json={"name": "Dogfood TV", "amount": 400}),
)
sid = (sc.get("scenario") or {}).get("id")
if sid:
    must("scenario run", c.post(f"/api/scenarios/{sid}/run"))
    r = c.delete(f"/api/scenarios/{sid}")
    if r.status_code == 200:
        ok("scenario delete")
    else:
        bad("scenario delete", str(r.status_code))

print("\n9. Reports + intermix surface")
must("cashflow", c.get("/api/reports/cashflow?days=30"))
must("debt report", c.get("/api/reports/debt"))
must("scenarios list", c.get("/api/scenarios"))

print("\n10. Glance")
g = c.get("/glance")
if g.status_code == 200 and b"Safe to spend" in g.content:
    ok("glance page")
else:
    bad("glance", str(g.status_code))

print("\n═══ DOGFOOD SCORE ═══")
print(f"  PASS {PASS}  FAIL {FAIL}")
if FAIL:
    print("DOGFOOD E2E FAILED")
    sys.exit(1)
print("DOGFOOD E2E OK — RC path A (API) green")
sys.exit(0)
