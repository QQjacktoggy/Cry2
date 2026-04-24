from __future__ import annotations

import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

import requests


def signed_balance_request(base_url: str, api_key: str, api_secret: str) -> tuple[int, str]:
    params = {
        "timestamp": str(int(time.time() * 1000)),
        "recvWindow": "5000",
    }
    query = urlencode(params)
    signature = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    response = requests.get(
        f"{base_url}/fapi/v2/balance",
        params={**params, "signature": signature},
        headers={"X-MBX-APIKEY": api_key},
        timeout=15,
    )
    return response.status_code, response.text[:500]


def main() -> None:
    api_key = os.environ["BINANCE_TESTNET_API_KEY"]
    api_secret = os.environ["BINANCE_TESTNET_API_SECRET"]

    for base_url in (
        "https://testnet.binancefuture.com",
        "https://demo-fapi.binance.com",
    ):
        status_code, body = signed_balance_request(base_url, api_key, api_secret)
        print(f"BASE={base_url}")
        print(f"STATUS={status_code}")
        print(f"BODY={body}")


if __name__ == "__main__":
    main()