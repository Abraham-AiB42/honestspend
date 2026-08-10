# HonestSpend marketing site

Static site for **https://honestspend.net** (privacy at **https://honestspend.net/privacy/**).

## Contents

| Path | Purpose |
|------|---------|
| `index.html` | Landing page with product copy + demo UI screenshots |
| `privacy/index.html` | Full privacy policy |
| `styles.css` | Shared styles |
| `assets/` | Favicon etc. |
| `_headers` | Cloudflare Pages security headers |
| `robots.txt` / `sitemap.xml` | SEO |

Screenshots are **accurate HTML/CSS mocks** of the WinUI app with **fictional demo data** (not live captures of a real bank account).

## Local preview

```powershell
cd site
npx --yes serve -l 5173
# open http://localhost:5173
```

## Deploy to Cloudflare Pages

### Automatic (preferred)

Push to `main` with changes under `site/**` runs  
`.github/workflows/deploy-site.yml` → project **honestspend** → **https://honestspend.net/**

GitHub secrets required: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`  
(See [docs/CI_CD.md](../docs/CI_CD.md).)

### Manual CLI

```powershell
$env:CLOUDFLARE_API_TOKEN = "…"
$env:CLOUDFLARE_ACCOUNT_ID = "…"
npx wrangler pages deploy site --project-name=honestspend --branch=main
```

### Privacy URL for Partner Center

`https://honestspend.net/privacy/`
