"""
Headless FYERS login via TOTP — generates an access token with NO browser.
Used by the GitHub Action (and can be run locally to refresh the token).

Requires in .env (or environment / GitHub Secrets):
    FYERS_APP_ID         e.g. LQFQ2OU7AQ-100
    FYERS_SECRET_KEY
    FYERS_REDIRECT_URI   must match the app
    FYERS_FY_ID          your FYERS login id, e.g. XN13999
    FYERS_PIN            your login PIN
    FYERS_TOTP_SECRET    the base32 TOTP key from myaccount.fyers.in (External 2FA TOTP)

On success it writes FYERS_ACCESS_TOKEN back into .env and prints OK.

One-time prerequisite: the app must have been authorised once in a browser
(you already did this via auth.py), and External 2FA TOTP must be enabled at
https://myaccount.fyers.in/ManageAccount.
"""
import base64
import sys
import time
from urllib.parse import parse_qs, urlparse

import pyotp
import requests
from fyers_apiv3 import fyersModel

from envtools import load_env, set_env_value

VAGATOR = "https://api-t2.fyers.in/vagator/v2"
TOKEN_URL = "https://api-t1.fyers.in/api/v3/token"


def _b64(value) -> str:
    return base64.b64encode(str(value).encode()).decode()


def _post(session, url, payload):
    r = session.post(url, json=payload, timeout=20)
    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"{url} -> HTTP {r.status_code}: {r.text[:200]}")
    return data


def generate_access_token(verbose=True) -> str:
    env = load_env()
    required = ["FYERS_APP_ID", "FYERS_SECRET_KEY", "FYERS_REDIRECT_URI",
                "FYERS_FY_ID", "FYERS_PIN", "FYERS_TOTP_SECRET"]
    missing = [k for k in required if not env.get(k)]
    if missing:
        raise SystemExit(f"Missing in .env / env: {', '.join(missing)}")

    app_id = env["FYERS_APP_ID"].strip()
    fy_id = env["FYERS_FY_ID"].strip()
    pin = env["FYERS_PIN"].strip()
    secret = env["FYERS_SECRET_KEY"].strip()
    redirect = env["FYERS_REDIRECT_URI"].strip()
    totp_secret = env["FYERS_TOTP_SECRET"].strip().replace(" ", "")

    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})

    # 1) send login OTP
    r1 = _post(s, f"{VAGATOR}/send_login_otp_v2", {"fy_id": _b64(fy_id), "app_id": "2"})
    if "request_key" not in r1:
        raise RuntimeError(f"send_login_otp failed: {r1}")
    request_key = r1["request_key"]

    # 2) verify TOTP (avoid using a code about to expire)
    totp = pyotp.TOTP(totp_secret)
    if totp.interval - (int(time.time()) % totp.interval) < 5:
        time.sleep(5)
    r2 = _post(s, f"{VAGATOR}/verify_otp", {"request_key": request_key, "otp": totp.now()})
    if "request_key" not in r2:
        raise RuntimeError(f"verify_otp failed: {r2}")
    request_key = r2["request_key"]

    # 3) verify PIN -> vagator access token
    r3 = _post(s, f"{VAGATOR}/verify_pin_v2",
               {"request_key": request_key, "identity_type": "pin", "identifier": _b64(pin)})
    vagator_token = (r3.get("data") or {}).get("access_token")
    if not vagator_token:
        raise RuntimeError(f"verify_pin failed: {r3}")

    # 4) exchange for an auth_code via the token endpoint
    s.headers.update({"Authorization": f"Bearer {vagator_token}"})
    r4 = _post(s, TOKEN_URL, {
        "fyers_id": fy_id,
        "app_id": app_id[:-4],            # strip the "-100" app-type suffix
        "redirect_uri": redirect,
        "appType": "100",
        "code_challenge": "",
        "state": "sample_state",
        "scope": "",
        "nonce": "",
        "response_type": "code",
        "create_cookie": True,
    })
    url = r4.get("Url") or r4.get("url")
    if not url:
        raise RuntimeError(f"token endpoint failed: {r4}")
    auth_code = parse_qs(urlparse(url).query).get("auth_code", [None])[0]
    if not auth_code:
        raise RuntimeError(f"no auth_code in redirect: {url}")

    # 5) auth_code -> final access token (standard SDK exchange)
    sess = fyersModel.SessionModel(
        client_id=app_id, secret_key=secret, redirect_uri=redirect,
        response_type="code", grant_type="authorization_code")
    sess.set_token(auth_code)
    resp = sess.generate_token()
    token = resp.get("access_token")
    if not token:
        raise RuntimeError(f"token exchange failed: {resp}")

    set_env_value("FYERS_ACCESS_TOKEN", token)
    if verbose:
        print("✅ FYERS access token generated and saved to .env")
    return token


if __name__ == "__main__":
    try:
        generate_access_token()
    except Exception as e:
        print(f"❌ login failed: {e}", file=sys.stderr)
        sys.exit(1)
