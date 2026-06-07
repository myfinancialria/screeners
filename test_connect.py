"""
Verify the Fyers connection: prints your profile, funds, and a sample quote.

    python3 test_connect.py

Run auth.py first if FYERS_ACCESS_TOKEN is empty / expired.
"""
import json

from fyers_apiv3 import fyersModel

from envtools import load_env


def main() -> None:
    env = load_env()
    app_id = env.get("FYERS_APP_ID", "").strip()
    token = env.get("FYERS_ACCESS_TOKEN", "").strip()

    if not token:
        raise SystemExit("No access token. Run:  python3 auth.py")

    fyers = fyersModel.FyersModel(client_id=app_id, token=token, is_async=False)

    print("== profile ==")
    print(json.dumps(fyers.get_profile(), indent=2))

    print("\n== funds ==")
    print(json.dumps(fyers.funds(), indent=2))

    print("\n== sample quote (NSE:SBIN-EQ, NSE:NIFTY50-INDEX) ==")
    print(json.dumps(fyers.quotes({"symbols": "NSE:SBIN-EQ,NSE:NIFTY50-INDEX"}), indent=2))


if __name__ == "__main__":
    main()
