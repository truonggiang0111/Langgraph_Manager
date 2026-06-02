from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, token: str = "", timeout: int = 30) -> dict[str, Any]:
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"content-type": "application/json; charset=utf-8"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {raw[:1200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot reach {url}: {exc.reason}") from exc
