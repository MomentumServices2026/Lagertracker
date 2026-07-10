# Momentum Services Lager Tracker — App Icons

AI-generated logo assets for home screen / PWA thumbnails. **No application code was changed.**

## Files

| File | Size | Use |
|------|------|-----|
| `icons/apple-touch-icon.png` | 180×180 | iPhone / iPad “Add to Home Screen” |
| `icons/apple-touch-icon-precomposed.png` | 180×180 | Older iOS fallback |
| `icons/favicon-32x32.png` | 32×32 | Browser tab icon |
| `icons/icon-192x192.png` | 192×192 | PWA / Android |
| `icons/icon-512x512.png` | 512×512 | PWA splash / high-res |
| `icons/momentum-lager-tracker-logo-source.png` | Original | Master copy |

## iPhone — Add to Home Screen

1. Open your app in **Safari** (not Chrome)
2. Tap **Share** → **Add to Home Screen**
3. iOS may still show a screenshot until icon `<link>` tags are added to the web app (optional future step)

To use this icon manually: after adding to home screen, iOS usually picks up `apple-touch-icon.png` only if the server serves it at `/apple-touch-icon.png`.

## Mac — Safari web app

1. Safari → **File → Add to Dock…** (or Add to Home Screen)
2. Same icon rules as above

## Using icons without changing Python code

**Option A — Vercel static files (vercel.json only, not app logic):**  
Add a rewrite or place copies in a `public/` folder if you enable static serving later.

**Option B — Manual:**  
Use `icons/icon-512x512.png` anywhere you need branding (presentations, shortcuts, etc.).

**Option C — Two lines in HTML later (optional):**  
If you want the icon on home screen automatically, only these tags are needed in the page `<head>` — no logic changes:

```html
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
```

That would require serving the PNG files from the server (small config change, not app logic).
