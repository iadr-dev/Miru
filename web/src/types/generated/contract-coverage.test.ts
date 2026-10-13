/**
 * Every API client that parses a JSON response must be covered by `contract-checks.ts`: either it
 * has an `Accepts<…>` line (compile-time check against the backend's documented response) or its
 * route is listed under "Not checkable yet". A new client with neither fails here, so a response
 * the frontend reads can't quietly escape the contract check.
 */
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const WEB = process.cwd();
const API_DIR = path.join(WEB, "src/lib/api");
const spec = JSON.parse(fs.readFileSync(path.join(WEB, "openapi.json"), "utf8")) as {
  paths: Record<string, Record<string, unknown>>;
};
const checks = fs.readFileSync(path.join(WEB, "src/types/generated/contract-checks.ts"), "utf8");

const specPaths = Object.keys(spec.paths).map((p) => ({
  p,
  re: new RegExp(
    `^${p
      .replace(/\{[^}]+\}/g, "__P__")
      .replace(/[.*+?^$()|[\]\\]/g, "\\$&")
      .replace(/__P__/g, "[^/]+")}$`,
  ),
}));

interface Client {
  file: string;
  fn: string;
  method: string;
  route: string;
}

/** The (method, route) every exported client function requests and parses with a `*Schema`. */
function parsedClients(): Client[] {
  const found: Client[] = [];
  for (const name of fs.readdirSync(API_DIR)) {
    if (!name.endsWith(".ts") || name.endsWith(".test.ts")) continue;
    const text = fs.readFileSync(path.join(API_DIR, name), "utf8");
    const sf = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true);
    for (const st of sf.statements) {
      if (!ts.isFunctionDeclaration(st) || !st.name || !st.body) continue;
      const literals: string[] = [];
      let method = "get";
      let parsesWithSchema = false;
      const visit = (n: ts.Node): void => {
        if (ts.isStringLiteralLike(n) && n.text.startsWith("/")) literals.push(n.text);
        if (ts.isTemplateExpression(n) && n.head.text.startsWith("/")) {
          literals.push(n.templateSpans.reduce((acc) => `${acc}X`, n.head.text));
        }
        if (
          ts.isPropertyAssignment(n) &&
          n.name.getText() === "method" &&
          ts.isStringLiteral(n.initializer)
        ) {
          method = n.initializer.text.toLowerCase();
        }
        if (
          ts.isCallExpression(n) &&
          ts.isPropertyAccessExpression(n.expression) &&
          /^(parse|safeParse)$/.test(n.expression.name.text) &&
          ts.isIdentifier(n.expression.expression) &&
          n.expression.expression.text.endsWith("Schema")
        ) {
          parsesWithSchema = true;
        }
        ts.forEachChild(n, visit);
      };
      visit(st.body);
      if (!parsesWithSchema) continue;
      for (const literal of literals) {
        const candidate = `/api/v1${literal.split("?")[0]}`.replace(/\/$/, "");
        const hit = specPaths.find((s) => s.re.test(candidate) && spec.paths[s.p]?.[method]);
        if (hit) {
          found.push({ file: name, fn: st.name.text, method, route: hit.p });
          break;
        }
      }
    }
  }
  return found;
}

describe("API contract coverage", () => {
  const clients = parsedClients();

  it("finds the API clients", () => {
    expect(clients.length).toBeGreaterThan(100); // a broken scan would pass everything below
  });

  it.each(clients.map((c) => [`${c.file} ${c.fn}`, c] as const))("%s is covered", (_label, c) => {
    const checked = checks.includes(`R<"${c.route}", "${c.method}">`);
    const listed = checks.includes(
      `//   ${c.method.toUpperCase()} ${c.route.replace("/api/v1", "")}`,
    );
    expect(
      checked || listed,
      `${c.method.toUpperCase()} ${c.route} (${c.fn}) has no line in types/generated/contract-checks.ts`,
    ).toBe(true);
  });
});
