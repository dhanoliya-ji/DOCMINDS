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
 *
 * It also checks the TypeScript SDK's copy (../sdk/typescript/src/schema.ts),
 * so the published client cannot drift from the API either.
 */

import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
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
  const sdk = "../sdk/typescript/src/schema.ts";
  if (existsSync(sdk) && normalise(fresh) !== normalise(sdk)) {
    console.error(`${sdk} is out of date: run \`npm run gen:api\` and commit the result.`);
    process.exit(1);
  }
  console.log("lib/api-types.ts and the SDK schema match openapi.json.");
} finally {
  rmSync(dir, { recursive: true, force: true });
}
