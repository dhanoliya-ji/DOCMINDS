"""Write the API's OpenAPI schema to frontend/openapi.json (input for `npm run gen:api`)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import app  # noqa: E402

target = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"
target.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"wrote {target}")
