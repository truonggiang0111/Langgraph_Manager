from __future__ import annotations

import json
import sys

from dotenv import load_dotenv

from .graph import build_graph


def main() -> int:
    load_dotenv()
    request = " ".join(sys.argv[1:]).strip() or "smoke test"
    app = build_graph()
    result = app.invoke({"request": request})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
