/**
 * check-api-types.mjs
 * ===================
 * `npm run check:api` -- fails when lib/api-types.ts no longer matches the
 * committed openapi.json, i.e. someone re-exported the schema (backend:
 * `python scripts/export_openapi.py`) but forgot `npm run gen:api`.
 *
 * CI-safe: it needs neither the backend nor the network, only the two
 * committed files. Line endings are normalised so a Windows checkout with
 * autocrlf does not report a false difference.
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const dir = mkdtempSync(join(tmpdir(), "api-types-"));
const fresh = join(dir, "api-types.ts");
try {
  execFileSync(
    process.execPath,
    ["node_modules/openapi-typescript/bin/cli.js", "openapi.json", "-o", fresh],
    { stdio: "ignore" }
  );
  const normalise = (path) => readFileSync(path, "utf8").replace(/\r\n/g, "\n");
  if (normalise(fresh) !== normalise("lib/api-types.ts")) {
    console.error("lib/api-types.ts is out of date: run `npm run gen:api` and commit the result.");
    process.exit(1);
  }
  console.log("lib/api-types.ts matches openapi.json.");
} finally {
  rmSync(dir, { recursive: true, force: true });
}
