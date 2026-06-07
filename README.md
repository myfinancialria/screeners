# NSE Bullish Screener (FYERS) + auto-published website

A FYERS-powered scanner that screens the **entire NSE** for bullish setups
(breakouts, pre-breakouts coiling under highs, uptrend pullbacks) and publishes
a daily-updated website via **GitHub Actions → GitHub Pages**. Also includes a
local paper-trading tool and an annotated chart/analysis CLI.

> Educational use only — not investment advice.

## Local quickstart
```bash
python3 -m pip install -r requirements.txt
cp .env.example .env            # fill in your FYERS app credentials
python3 auth.py                 # one-time browser login (or use fyers_login.py)
python3 analyze.py NSE:SBIN-EQ --chart      # single-stock analysis + chart
python3 scan_all.py --setup pre_breakout    # scan all NSE for early setups
python3 build_site.py --universe nifty50    # build the website into site/
```

## Daily website on GitHub Pages (hands-off)

The workflow [.github/workflows/daily-scan.yml](.github/workflows/daily-scan.yml)
runs Mon–Fri at **16:45 IST**, logs in to FYERS headlessly via TOTP, scans all
of NSE, and deploys `site/` to Pages.

### One-time setup
1. **Enable TOTP** on your FYERS account: <https://myaccount.fyers.in/ManageAccount>
   → enable *External 2FA TOTP* and **save the base32 TOTP key**.
2. **Create a GitHub repo** and push this folder:
   ```bash
   git remote add origin git@github.com:<you>/<repo>.git
   git push -u origin main
   ```
3. **Add repository Secrets** (Settings → Secrets and variables → Actions → New secret):

   | Secret | Value |
   |---|---|
   | `FYERS_APP_ID` | e.g. `LQFQ2OU7AQ-100` |
   | `FYERS_SECRET_KEY` | your app secret |
   | `FYERS_REDIRECT_URI` | the redirect URI registered on the app |
   | `FYERS_FY_ID` | your FYERS login id, e.g. `XN13999` |
   | `FYERS_PIN` | your login PIN |
   | `FYERS_TOTP_SECRET` | the base32 TOTP key from step 1 |

4. **Enable Pages**: Settings → Pages → **Source: GitHub Actions**.
5. **Test it**: Actions tab → *Daily NSE Bullish Screener* → **Run workflow**.
   When it finishes, your site is at `https://<you>.github.io/<repo>/`.

### Before relying on CI, test the headless login locally
Fill `FYERS_FY_ID`, `FYERS_PIN`, `FYERS_TOTP_SECRET` in `.env`, then:
```bash
python3 fyers_login.py     # should print: ✅ access token generated
```
If that works locally, it will work in the Action.

## Security
- **`.env` is gitignored** — credentials never get committed. In CI they come
  from encrypted GitHub Secrets.
- The published **website is public**; it shows only screen results, no secrets.
- Tokens expire daily and are regenerated each run by `fyers_login.py`.

## What's in here
| File | Purpose |
|---|---|
| `fyers_login.py` | headless TOTP login → access token |
| `scan_all.py` | concurrent full-NSE scanner (CLI) |
| `build_site.py` | generate the Pages website (`site/`) |
| `daily_scan.py` | alternative: email the report (local launchd) |
| `analyze.py` | single-stock / universe analysis + charts |
| `signals.py` `candles.py` `levels.py` `indicators.py` | the scoring engine |
| `paper.py` | local paper-trading with live prices |
| `SCANNER.md` | scanner documentation |
