from __future__ import annotations

import requests


def check_chrome_debug_endpoint(debug_url: str = "http://127.0.0.1:9222") -> bool:
    try:
        response = requests.get(f"{debug_url.rstrip('/')}/json/version", timeout=2)
    except requests.RequestException:
        return False
    return response.ok
