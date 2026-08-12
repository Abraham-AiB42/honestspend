# Brand

**Product name:** **HonestSpend**

## Meaning

| Piece | Idea |
|-------|------|
| **Honest** | Local books you control — no bank-password storage, no paid feed lock-in |
| **Spend** | Safe to spend · intentional 0% float · never bounce checking |

**Tagline:** *Honest books. Safe to spend.*

## Clearance note (public research, not legal advice)

Confirm domain + USPTO before a hard commercial launch under this name.

## Technical map

| Layer | Name |
|-------|------|
| Display / UI / docs | **HonestSpend** |
| Python package | `honestspend` |
| PyPI / CLI | `honestspend` (legacy aliases: `floatpile`, `ledgerring`, `financial-os`) |
| WinUI project / EXE | `HonestSpend.WinUI` / `HonestSpend.WinUI.exe` |
| Data dir | `~/.HonestSpend` (legacy `~/.financial-os` still opened if present) |
| Database file | `honestspend.db` (legacy `financial_os.db` still opened if present) |
| Env prefix | `FOS_*` (stable) |
| Task Scheduler | `HonestSpend-AutoBackup`, `HonestSpend-Digest`, `HonestSpend-ImportInbox` |

## History

| Name | Notes |
|------|--------|
| **HonestSpend** | Current product name (everywhere) |
| **Floatpile** | Working alpha — CLI alias only |
| **LedgerRing** | Earlier name — CLI alias only |
