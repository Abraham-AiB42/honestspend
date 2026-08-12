# HonestSpend Privacy Policy

**Last updated:** 2026-08-10  
**Product:** HonestSpend (local-first personal finance app)  
**Publisher:** Agency in Box 42

This policy describes how HonestSpend handles information when you use the desktop app (including Microsoft Store and sideload builds).

## Short version

- Your money data stays **on your device** by default.
- We do **not** operate a cloud ledger or bank password vault.
- Optional features that talk to the internet use **keys you supply** (BYOK).

## What we store locally

On your PC (default folder `%USERPROFILE%\.HonestSpend`, or a path you choose in Settings):

- Accounts, transactions, categories, and balances you enter or import  
- App settings (API URL, data folder, optional backend path)  
- Local backups you create  
- Engine logs (for troubleshooting)  

Uninstalling the Store package may not delete this data folder (by design so you can keep books across reinstalls). Delete the folder manually if you want a full wipe.

## What we do **not** collect

- Bank website usernames or passwords  
- Your ledger data on our servers (there is no HonestSpend cloud ledger)  
- Advertising identifiers for ad networks  

## Optional internet features

| Feature | Data sent | When |
|---------|-----------|------|
| **BYOK Plaid** | Your Plaid client id/secret and bank link tokens you authorize | Only if you enable bank link with **your** Plaid keys |
| **BYOK Grok** | Transaction payee text for categorization suggestions | Only if you enable AI categorize with **your** xAI key |
| **Microsoft Store** | Standard Store install/update diagnostics to Microsoft | If you install from the Store |

The free path (CSV / OFX / PDF import) works **offline** after install.

## Children

HonestSpend is not directed at children under 13. Do not use the app to store a child’s data without appropriate parental control.

## Your choices

- Use **Import** only (no bank APIs)  
- Turn off optional BYOK features  
- Change **Data folder** (e.g. OneDrive) in Get started or Settings  
- Optional **app lock** (PIN / password / Windows Hello) — device-local only; does not encrypt the database file by itself  
- Export/delete data via backups and local file tools  
- Uninstall the app  

## App lock + database encryption

If you enable an app lock (PIN / password / Windows Hello), HonestSpend:

- Stores a **one-way hash** of your PIN/password (or a Hello flag) in Windows app settings on **this device**
- **Encrypts your ledger at rest** (AES-256-GCM sealed file) when the app seals books — the same secret unwraps the database key

Lock secrets and database keys are **not** uploaded and are **not** included in ordinary SQLite backup zips. **If you forget your PIN/password, sealed books cannot be recovered.** Clearing the lock does not delete books; disabling encryption after unlock restores plaintext books on disk.

## Third parties

- **Microsoft** — if you install from the Microsoft Store (their privacy terms apply to Store services).  
- **Plaid / xAI** — only when you configure BYOK; subject to their policies.  

## Contact

Open an issue on the project repository:  
https://github.com/Abraham-AiB42/honestspend  

**Public privacy policy URL (Store / Partner Center):**  
https://honestspend.net/privacy/

(Source copy lives in `docs/PRIVACY.md` and is published as HTML at `site/privacy/`.)

## Changes

We may update this policy; the “Last updated” date will change. Continued use after an update means you accept the revised policy for local freeware use; Store users may see listing updates when a new package is certified.
