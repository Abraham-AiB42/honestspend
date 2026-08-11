"""
1.0 RC dogfood path (API-level) — household journey without WinUI.
Mirrors docs/RC_1.0.md section A as automated checks.
"""
from __future__ import annotations

import sys
import tempfile
from decimal import Decimal
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

print("\n2b. Money-in honesty (CSV ending bal + trust)")
accts = must("list accounts", c.get("/api/accounts"))
cash = next(
    (
        a
        for a in (accts if isinstance(accts, list) else [])
        if a.get("is_cash_for_ifpp") or a.get("kind") == "checking"
    ),
    None,
)
if not cash:
    bad("cash account", "none after first-run")
else:
    # Books open at 6500; bank ending 6000 after small spend → set_books CTA
    csv_body = (
        "Date,Description,Amount,Balance\n"
        "2026-08-01,COFFEE,-4.50,6000.00\n"
    )
    r = c.post(
        f"/api/import/bank-csv?account_id={cash['id']}&auto_categorize=false&amount_sign=bank",
        files={"file": ("dogfood.csv", csv_body, "text/csv")},
    )
    if r.status_code != 200:
        bad("csv import", f"HTTP {r.status_code} {r.text[:120]}")
    else:
        body = r.json()
        ok("csv import", f"created {body.get('transactions_created')}")
        actions = {st.get("action") for st in (body.get("next_steps") or [])}
        if "set_books_from_bank" in actions or body.get("institution_balance_set"):
            ok("post-import bank bal / set_books CTA")
        else:
            bad("post-import honesty CTA", str(actions)[:80])
        trust = c.post(
            f"/api/reconcile/{cash['id']}/trust",
            json={"trust": "institution"},
        )
        if trust.status_code != 200:
            bad("trust institution", f"HTTP {trust.status_code}")
        else:
            bal = Decimal(str(trust.json().get("books_balance") or "0"))
            if bal == Decimal("6000.00") or bal == Decimal("6000"):
                ok("trust institution", f"books={bal}")
            else:
                bad("trust institution books", f"expected 6000.00 got {bal}")
        home2 = must("home after trust", c.get("/api/home/simple"))
        bb = home2.get("books_brief") or {}
        if bb.get("primary_action") != "set_books_from_bank":
            ok("books_brief no longer set_books after trust")
        else:
            bad("books_brief still set_books", bb.get("title", ""))

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

print("\n8. Setup wizard surfaces + budgets + Can I buy")
setup = must("setup state", c.get("/api/setup/state"))
if setup.get("phase") is not None:
    ok("setup phase", str(setup.get("phase")))
scash = must("setup cash", c.get("/api/setup/cash"))
if "accounts" in scash or "types" in scash:
    ok("setup cash types", str(len(scash.get("types") or [])))
sdisc = must("setup discover", c.get("/api/setup/discover"))
if "proposals" in sdisc or "count" in sdisc:
    ok("setup discover", f"count={sdisc.get('count', 0)}")
srec = must("setup recurring", c.get("/api/setup/recurring"))
if "suggestions" in srec:
    ok("setup recurring")
scat = must("setup categorize", c.get("/api/setup/categorize"))
if "uncategorized_count" in scat or "confirm_queue" in scat:
    ok("setup categorize", f"uncat={scat.get('uncategorized_count')}")
sbud = must("setup budgets", c.get("/api/setup/budgets?seed_if_empty=true"))
if "rules" in sbud or "count" in sbud:
    ok("setup budgets", f"rules={sbud.get('count', 0)}")
sbuf = must("setup buffers", c.get("/api/setup/buffers"))
if "total_buffer" in sbuf or "effective_buffer" in sbuf:
    ok("setup buffers", f"eff={sbuf.get('effective_buffer')}")
pcred = must("plaid credentials status", c.get("/api/plaid/credentials"))
if "item_limit" in pcred or "configured" in pcred:
    ok("plaid creds surface", f"limit={pcred.get('item_limit', 10)}")
aic = must("ai credentials", c.get("/api/ai/credentials"))
if "providers" in aic:
    ok("ai providers", str(len(aic.get("providers") or [])))

bst = must("budget status", c.get("/api/budgets/status"))
if "items" in bst or "reserve_total" in bst:
    ok("budget status", f"reserve={bst.get('reserve_total', '?')}")
else:
    bad("budget status", str(bst)[:120])
bsug = must("budget suggestions", c.get("/api/budgets/suggestions"))
if "suggestions" in bsug or isinstance(bsug, dict):
    ok("budget suggestions")
buy = must("pre-purchase", c.post("/api/pre-purchase", json={"amount": 80, "prefer": "auto"}))
if buy.get("verdict"):
    ok("buy verdict", buy["verdict"])
snap = buy.get("ifpp_snapshot") or {}
if "safe_to_spend" in snap or "cash_spendable" in snap:
    ok("buy safe_to_spend snapshot", str(snap.get("safe_to_spend", snap.get("cash_spendable"))))
else:
    bad("buy ifpp_snapshot", str(snap)[:80])
# Prefer at least one cash option with account_id when accounts exist
cash_named = [
    o for o in (buy.get("options") or [])
    if o.get("method") == "cash" and o.get("account_id")
]
if cash_named or not cash:
    ok("buy cash account options", str(len(cash_named)))
else:
    bad("buy cash account options", "none")
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
