# Client-first policy

**Primary product surface is a native desktop client.** The fiscal engine is Python; the **app people use** is not a website.

## Order of preference

| Priority | Surface | Role |
|----------|---------|------|
| **1** | **WinUI 3** (`LedgerRing.WinUI`) | Daily product — Simple + Full books |
| **2** | **System tray** (Python process, launched by client) | Hover Safe to spend · toasts · open **WinUI** |
| **3** | **CLI** (`ledgerring`) | Power users · scripts · automation |
| **4** | **Glance HTML** | Emergency / Mac-Linux-phone **read** shell until native clients exist |

## Explicit non-goals

- **No PWA** as a product track (no “install this web app”)  
- No Electron/Tauri wrapper as the primary Windows app (WinUI stays)  
- No “mobile web first” redesign of Simple mode  

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

## 1.x roadmap alignment

H3 automation and nerd tools ship **in WinUI** first.  
See [DREAM_ROADMAP.md](./DREAM_ROADMAP.md) · [CLIENTS.md](./CLIENTS.md).
