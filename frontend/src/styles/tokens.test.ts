import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");
// tokens.css is the one place colours are defined.
const ALLOWED = new Set(["styles/tokens.css"]);

function files(directory: string): string[] {
  return readdirSync(directory).flatMap(name => {
    const path = join(directory, name);
    return statSync(path).isDirectory() ? files(path) : /\.(css|tsx?)$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
  });
}

describe("colour tokens", () => {
  it("keeps raw hex and rgb colours out of components and styles", () => {
    const offenders: string[] = [];
    for (const path of files(SRC)) {
      const name = relative(SRC, path).replace(/\\/g, "/");
      if (ALLOWED.has(name)) continue;
      const text = readFileSync(path, "utf8")
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/(["'`])#\/[^"'`]*\1/g, "")
        .replace(/href=\{?[^\s>]+/g, "");
      for (const match of text.matchAll(/#[0-9a-fA-F]{3,8}\b|\brgba?\(/g)) offenders.push(`${name}: ${match[0]}`);
    }
    expect(offenders).toEqual([]);
  });
});
