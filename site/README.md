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

### Option A — Wrangler (CLI)

```powershell
# one-time: npx wrangler login
# or set CLOUDFLARE_API_TOKEN with Pages:Edit

cd <repo-root>
npx wrangler pages deploy site --project-name=honestspend --branch=main
```

Then in Cloudflare Dashboard → Pages → honestspend → Custom domains → add `honestspend.net` (and `www` if desired). Point DNS as Cloudflare instructs.

### Option B — Dashboard

1. Cloudflare Dashboard → Workers & Pages → Create → Pages → Upload assets (or connect Git).
2. Project name: `honestspend`
3. Upload the contents of `site/` (or set build output directory to `site` with no build command if using Git).
4. Custom domain: `honestspend.net`

### Privacy URL for Partner Center

`https://honestspend.net/privacy/`
