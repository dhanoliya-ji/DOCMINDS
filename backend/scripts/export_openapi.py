"""
export_openapi.py
=================
Writes the API's OpenAPI schema to frontend/openapi.json, the file the
frontend's typed client is generated from (`npm run gen:api`, which runs
openapi-typescript into frontend/lib/api-types.ts).

    cd backend && python scripts/export_openapi.py          # rewrite it
    cd backend && python scripts/export_openapi.py --check  # exit 1 if stale

The schema comes from `app.openapi()` -- the same document FastAPI serves at
/openapi.json -- so it needs no running server and no database. The output
is sorted and indented, so a change to one endpoint is a small, readable diff.
Commit the file whenever an endpoint or schema changes.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402

TARGET = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"


def render() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != text:
            print(f"{TARGET} is out of date: run `python scripts/export_openapi.py`.")
            return 1
        print("openapi.json is up to date.")
        return 0
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
