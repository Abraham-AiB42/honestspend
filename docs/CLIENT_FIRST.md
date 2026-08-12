# Client-first policy

**Primary product surface is a native desktop client.** The fiscal engine is Python; the **app people use** is not a website.

## Order of preference

| Priority | Surface | Role |
|----------|---------|------|
| **1** | **WinUI 3** (`HonestSpend.WinUI`) | Daily product — Simple + Full books |
| **2** | **System tray** (Python process, launched by client) | Hover Safe to spend · toasts · open **WinUI** |
| **3** | **CLI** (`honestspend`) | Power users · scripts · automation |
| **4** | **Glance HTML** | Emergency / Mac-Linux-phone **read** shell until native clients exist |

## Explicit non-goals

- **No PWA** as a product track (no “install this web app”)  
- No Electron/Tauri wrapper as the primary Windows app (WinUI stays)  
- No “mobile web first” redesign of Simple mode  
- **No paid cloud bank feed** we operate — full freeware; Plaid/Grok are **BYOK only**  
- **No storing bank website passwords** (CSV/OFX/statements + optional OAuth tokens only)  

## Freeware money-in

| Path | Role |
|------|------|
| **Import CSV** | Default free path · optional Balance column / ending balance → Reconcile |
| **Import OFX/QFX** | Quicken-style downloads · FITID dedupe · **LEDGERBAL → Reconcile** |
| **PDF statements** | Best-effort text extract (prefer CSV/OFX when offered) |
| **Bank guides** | Login links + download steps (no passwords) |
| **Inbox folder** | Drop `.csv` / `.ofx` / `.qfx` / `.pdf` → `honestspend import-inbox` / daily task · next_steps after process |
| **OFX ACCTID match** | First OFX import learns account id; later drops auto-route without nickname in filename |
| **Import reminders** | User-chosen cadence: off · daily · weekly · monthly |
| **Post-import next steps** | After CSV / OFX / PDF: **Set Safe to spend from bank** · Sort charges · Home |
| **Import → books** | New rows update `current_balance` (IFPP); bank ending bal → institution + one-tap trust |
| **BYOK Plaid** | Optional live link with *user’s* keys |
| **BYOK Grok** | Optional AI categorize with *user’s* xAI key |
| **Rules categorizer** | Always free, offline |

## Engine vs client

- **Engine** = source of fiscal truth (API on localhost).  
- **Client** = UX, wizards, navigation, packaging.  
- Clients **never** re-implement IFPP math; they call the API.

## Future platforms

When we leave Windows-primary:

1. Native Mac client (not Safari PWA)  
2. Native Linux client (not Chromium shell as the product)  
3. Mobile **native** only if warranted — not a responsive Glance stretch goal sold as an app  

Glance may improve as a **lab/read tool**; it does not replace client-first delivery.

## Tray → desktop deep-links

Tray menu opens the **WinUI** EXE (from `winui.path`), not Glance:

| Tray item | CLI / request |
|-----------|----------------|
| Open HonestSpend | (home) |
| Sort charges | `--page review` |
| Reports | `--page reports` |
| Settings | `--page settings` |

A second launch writes `~/.HonestSpend/winui.navigate` (or `FOS_DATA_DIR`) and signals the running instance to show + navigate. Cold start: same `--page` flag on the EXE.

## 1.x roadmap alignment

H3 automation and nerd tools ship **in WinUI** first.  
See [DREAM_ROADMAP.md](./DREAM_ROADMAP.md) · [CLIENTS.md](./CLIENTS.md).
