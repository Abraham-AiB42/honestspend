# Store certification analysis (support files)

**Source:** `Downloads\cert-report-HonestSpend-support-files`  
**Package under test:** `AgencyinBox42.HonestSpend_1.0.32.0_x64__fjke548ww9m4e`

---

## 1. Policy 10.1.1.11 — On Device Tiles

**Evidence:** `HonestSpend_10.1.1.11_screenshot1.png`

Shows Start / apps list with:

- Label: **HonestSpend** · “Recently added”
- Icon: **default broken-tile glyph** (square with diagonal X)

That is **not** the HonestSpend ledger mark. Windows could not resolve package VisualElements logos for the cert machine’s scale (commonly **100% DPI**), so it fell back to the system placeholder. Cert then rejects the tile under **10.1.1.11** (“tiles must uniquely represent your product” / not foreign or default imagery).

### Root cause (1.0.32 package)

| Factor | Why it breaks |
|--------|----------------|
| Only **scale-200** (or incomplete) assets | Cert VM at 100% has no scale-100 match → missing image |
| `StoreLogo` wrong size / missing | Package `Properties/Logo` weak |
| Assets not all copied into MSIX | Relative Content without output copy |

### Fix (repo now)

- `scripts/generate-store-tiles.py` → scale-100 **and** scale-200, brand `#0F2744`, wide **HonestSpend** wordmark  
- `Package.appxmanifest` → solid `BackgroundColor`, `Square71x71`, `ShowNameOnTiles`, version **1.0.55.0**  
- `HonestSpend.WinUI.csproj` → `Assets\**` with `CopyToOutputDirectory=PreserveNewest`  

---

## 2. Crash after launch

**Evidence:** `HonestSpend_crashlog.evtx` (1 event)

```
Faulting application: HonestSpend.WinUI.exe  (version field 1.0.0.0)
Faulting module:     Microsoft.UI.Xaml.dll  3.2.3.0
Exception code:      0xc000027b   (STATUS_STOWED_EXCEPTION — WinRT/XAML)
Path:                ...\AgencyinBox42.HonestSpend_1.0.32.0_x64__...\HonestSpend.WinUI.exe
```

### Interpretation

- Crash is in **XAML UI load**, not Python engine.
- **0xc000027b** = stowed exception; usually unhandled failure while building the first `Window`.
- Matches known WinUI patterns: bad **ImageIconSource** / icon path / missing asset during `InitializeComponent`.

### Likely trigger in MainWindow (fixed)

```xml
<!-- WAS: can fail packaged / wrong scale path during XAML parse -->
<ImageIconSource ImageSource="Assets/Square44x44Logo.scale-200.png" />
```

Comment in tree already noted `.ico` → **E_POINTER / XAML load crash**. Same class of failure with a bad/missing PNG path in Store package.

### Fix (repo now)

- Removed TitleBar `ImageIconSource` from XAML  
- Set icon only in code via absolute `AppContext.BaseDirectory\Assets\AppIcon.ico`  
- Window activates before engine; soft-fail `OnLaunched` / `NavView_Loaded`  
- MSIX build **requires** `engine-portable.zip` (separate from this XAML crash, still required for usable app)

---

## 3. Resubmit checklist

1. `python scripts/generate-store-tiles.py`  
2. `.\scripts\package-msix.ps1` → package **≥ 1.0.55.0** (not 1.0.32)  
3. Sideload on clean 100% DPI VM:  
   - Start tile = navy ledger (not X)  
   - App opens without Application Error 1000  
4. Partner Center: new package + paste `packaging/msix/Notes-for-certification.txt`  

---

## 4. What “already fixed” vs what cert actually tested

Cert tested **1.0.32.0**. Many launch/engine soft-fail improvements landed after that, but the **TitleBar ImageIconSource** and **scale-100 tiles** were still open until this analysis. Resubmit must use a **new MSIX** built after these fixes — not the same 1.0.32 binary.
