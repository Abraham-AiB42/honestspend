"""Static how-to guides: download transactions CSV/OFX from major banks (freeware path).

No bank logins stored — links + steps only. Steps change; treat as best-effort tips.
"""

from __future__ import annotations

from typing import Any

# Institution guides for Import UI / first-run. Prefer official online banking URLs.
_GUIDES: list[dict[str, Any]] = [
    {
        "id": "chase",
        "name": "Chase",
        "login_url": "https://secure.chase.com/",
        "export_types": ["csv"],
        "focus": "transactions",
        "steps": [
            "Sign in to Chase online or the website (not always available in the mobile app).",
            "Open the account → Account activity (or Download account activity).",
            "Choose a date range → Download → CSV (or QFX if offered).",
            "Save the file into your Floatpile inbox folder, or Import → Pick CSV in the app.",
        ],
        "notes": "Business and personal menus differ slightly. Look for “Download” near the activity table.",
    },
    {
        "id": "amex",
        "name": "American Express",
        "login_url": "https://www.americanexpress.com/",
        "export_types": ["csv"],
        "focus": "transactions",
        "steps": [
            "Sign in → select your card.",
            "Statements & Activity (or View Transactions).",
            "Download → CSV (or Excel / OFX if listed).",
            "Import into Floatpile and map to your card account.",
        ],
        "notes": "CSV is ideal for mid-cycle refresh; PDF statements are better for month-close.",
    },
    {
        "id": "capital_one",
        "name": "Capital One",
        "login_url": "https://www.capitalone.com/",
        "export_types": ["csv"],
        "focus": "transactions",
        "steps": [
            "Sign in → Accounts → pick checking or card.",
            "Account details / Transactions → Download or Export.",
            "Choose CSV for the date range you need.",
            "Drop into inbox or Import CSV in Floatpile.",
        ],
        "notes": "Some products label export under “View statements” → Download transactions.",
    },
    {
        "id": "bank_of_america",
        "name": "Bank of America",
        "login_url": "https://www.bankofamerica.com/",
        "export_types": ["csv", "qfx"],
        "focus": "transactions",
        "steps": [
            "Sign in → Accounts → select account.",
            "Download (near activity) → choose CSV or Quicken (QFX).",
            "Pick date range → Download.",
            "Import CSV in Floatpile (QFX/OFX support may expand later — CSV is preferred today).",
        ],
        "notes": "CSV works with Floatpile’s bank importer today.",
    },
    {
        "id": "wells_fargo",
        "name": "Wells Fargo",
        "login_url": "https://www.wellsfargo.com/",
        "export_types": ["csv"],
        "focus": "transactions",
        "steps": [
            "Sign in → Account Activity.",
            "Download / Export → CSV (or spreadsheet).",
            "Select period → download.",
            "Import and assign to the matching Floatpile account.",
        ],
        "notes": "If you only see PDF statements, use those for monthly reconcile; ask for “download transactions” for weekly refresh.",
    },
    {
        "id": "citi",
        "name": "Citi",
        "login_url": "https://online.citi.com/",
        "export_types": ["csv"],
        "focus": "transactions",
        "steps": [
            "Sign in → account → Account details / Activity.",
            "Download transactions → CSV or Quicken format.",
            "Save and import into Floatpile.",
        ],
        "notes": "Credit cards often have a clear “Download” control above the transaction list.",
    },
    {
        "id": "us_bank",
        "name": "U.S. Bank",
        "login_url": "https://www.usbank.com/",
        "export_types": ["csv", "ofx"],
        "focus": "transactions",
        "steps": [
            "Sign in → Accounts → Account details.",
            "Download → CSV or OFX.",
            "Import CSV in Floatpile.",
        ],
        "notes": "",
    },
    {
        "id": "discover",
        "name": "Discover",
        "login_url": "https://www.discover.com/",
        "export_types": ["csv"],
        "focus": "transactions",
        "steps": [
            "Sign in → Activity.",
            "Download → CSV (or Quicken).",
            "Import into your Discover card account in Floatpile.",
        ],
        "notes": "",
    },
    {
        "id": "fidelity",
        "name": "Fidelity (cash / brokerage)",
        "login_url": "https://www.fidelity.com/",
        "export_types": ["csv"],
        "focus": "transactions",
        "steps": [
            "Sign in → Accounts & Trade → select account.",
            "Activity / History → Download → CSV.",
            "Map to the matching Floatpile account (cash or brokerage as you track it).",
        ],
        "notes": "Investment lots are not a full portfolio product — treat as cashflow import.",
    },
    {
        "id": "generic",
        "name": "Other bank / credit union",
        "login_url": None,
        "export_types": ["csv"],
        "focus": "both",
        "steps": [
            "Sign in to online banking in a browser.",
            "Open the account → Activity, Transactions, or Statements.",
            "Look for Download, Export, or “Download transactions” → prefer CSV.",
            "If only PDF statements exist, download those for monthly reconcile; use CSV when available for mid-cycle Safe to spend.",
            "Save to your Floatpile inbox folder or use Import → Pick CSV.",
        ],
        "notes": "Search your bank’s help for “download transactions CSV” or “export OFX”.",
    },
]


def list_bank_guides() -> dict[str, Any]:
    return {
        "title": "Download from your bank",
        "principle": (
            "Floatpile never stores bank passwords. You download a file; we import it. "
            "Optional live link = your own Plaid keys."
        ),
        "inbox_hint": (
            "Drop CSV files into the inbox folder and run Import inbox (or the daily scheduled task)."
        ),
        "guides": list(_GUIDES),
    }


def get_bank_guide(guide_id: str) -> dict[str, Any] | None:
    gid = (guide_id or "").strip().lower()
    for g in _GUIDES:
        if g["id"] == gid:
            return g
    return None
