"""
One-time login for the Fyers API (v3).

Run this in YOUR OWN terminal (it needs a browser login):

    python3 auth.py

It will:
  1. print a login URL  -> open it, log in, approve
  2. your browser lands on your redirect URI with ?auth_code=... in the address bar
  3. paste that whole URL (or just the auth_code) back here
  4. it exchanges the code for an access token and saves it into .env

The access token is valid for the trading day (expires ~next morning),
so re-run this when it expires.
"""
from urllib.parse import urlparse, parse_qs

from fyers_apiv3 import fyersModel

from envtools import load_env, set_env_value


def main() -> None:
    env = load_env()
    app_id = env.get("FYERS_APP_ID", "").strip()
    secret = env.get("FYERS_SECRET_KEY", "").strip()
    redirect = env.get("FYERS_REDIRECT_URI", "").strip()

    if not app_id or app_id == "your_app_id_here" or not secret:
        raise SystemExit("Fill FYERS_APP_ID and FYERS_SECRET_KEY in .env first.")

    session = fyersModel.SessionModel(
        client_id=app_id,
        secret_key=secret,
        redirect_uri=redirect,
        response_type="code",
        grant_type="authorization_code",
    )

    print("\n1) Open this URL in your browser, log in, and approve:\n")
    print("   " + session.generate_authcode() + "\n")
    print("2) After approving you'll be redirected to your redirect URI.")
    print("   Copy the FULL address-bar URL (or just the auth_code).\n")

    pasted = input("Paste redirected URL or auth_code here: ").strip()

    # Accept either a full URL (extract auth_code) or the raw code.
    if pasted.startswith("http"):
        qs = parse_qs(urlparse(pasted).query)
        auth_code = (qs.get("auth_code") or qs.get("code") or [""])[0]
    else:
        auth_code = pasted

    if not auth_code:
        raise SystemExit("Could not find an auth_code in what you pasted.")

    session.set_token(auth_code)
    resp = session.generate_token()

    token = resp.get("access_token")
    if not token:
        raise SystemExit(f"Token exchange failed: {resp}")

    set_env_value("FYERS_ACCESS_TOKEN", token)
    print("\n✅ Access token saved to .env. You can now run:  python3 test_connect.py")


if __name__ == "__main__":
    main()
