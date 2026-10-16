import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// Reads the real tokens.css so the check can never drift from what ships (Vitest stubs CSS imports, so read from disk).
const css = readFileSync(join(__dirname, "tokens.css"), "utf8");

function block(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`Missing ${selector}`);
  const body = css.slice(start, css.indexOf("\n}", start));
  return Object.fromEntries([...body.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(match => [match[1], match[2].trim()]));
}

const light = block(':root, :root[data-theme="light"]');
const themes = { light, dark: { ...light, ...block(':root[data-theme="dark"]') } };

type RGBA = [number, number, number, number];

function resolve(tokens: Record<string, string>, name: string): string {
  const value = tokens[name];
  if (value === undefined) throw new Error(`Unknown token ${name}`);
  const reference = /^var\((--[\w-]+)\)$/.exec(value);
  return reference ? resolve(tokens, reference[1]) : value;
}

function parse(value: string): RGBA {
  const hex = /^#([0-9a-f]{6})$/i.exec(value);
  if (hex) return [0, 2, 4].map(offset => parseInt(hex[1].slice(offset, offset + 2), 16)).concat(1) as RGBA;
  const rgba = /^rgba?\(([^)]+)\)$/.exec(value);
  if (rgba) { const [r, g, b, a = "1"] = rgba[1].split(",").map(part => part.trim()); return [Number(r), Number(g), Number(b), Number(a)]; }
  throw new Error(`Not a colour: ${value}`);
}

const over = ([r, g, b, a]: RGBA, [br, bg, bb]: RGBA): RGBA => [r * a + br * (1 - a), g * a + bg * (1 - a), b * a + bb * (1 - a), 1];

function luminance([r, g, b]: RGBA) {
  const channel = (value: number) => { const c = value / 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrast(foreground: RGBA, background: RGBA) {
  const [high, low] = [luminance(foreground), luminance(background)].sort((x, y) => y - x);
  return (high + 0.05) / (low + 0.05);
}

/** Contrast of a text token on a background token; translucent colours are composited over `base` (the page surface). */
function ratio(tokens: Record<string, string>, text: string, background: string, base = "--surface") {
  const page = over(parse(resolve(tokens, base)), parse(resolve(tokens, "--background")));
  const fill = over(parse(resolve(tokens, background)), page);
  return contrast(over(parse(resolve(tokens, text)), fill), fill);
}

// Every text-on-surface pairing the UI actually renders.
const PAIRS: Array<[text: string, background: string, base?: string]> = [
  ["--foreground", "--background"], ["--foreground", "--surface"], ["--foreground", "--surface-raised"], ["--foreground", "--surface-muted"], ["--foreground", "--muted"],
  ["--foreground", "--topbar-bg", "--background"],
  ["--muted-foreground", "--background"], ["--muted-foreground", "--surface"], ["--muted-foreground", "--surface-raised"], ["--muted-foreground", "--surface-muted"],
  ["--secondary-foreground", "--surface"], ["--secondary-foreground", "--surface-muted"], ["--secondary-foreground", "--muted"],
  ["--tab-foreground", "--muted"],
  ["--primary-strong", "--surface"], ["--primary-strong", "--primary-soft"],
  ["--primary-foreground", "--primary-solid"], ["--primary-foreground", "--primary-solid-hover"],
  ["--violation-text", "--violation-bg"], ["--violation-text", "--surface"],
  ["--review-text", "--review-bg"], ["--pass-text", "--pass-bg"],
  ["--critical-text", "--critical-bg"], ["--na-text", "--na-bg"],
  // Sign-in page: story panel, label card, rulers, CTA gradient ends, and status pills on the page background.
  ["--foreground", "--story-bg"], ["--muted-foreground", "--story-bg"], ["--muted-foreground", "--ruler-bg"],
  ["--foreground", "--label-card"], ["--muted-foreground", "--label-card"],
  ["--pass-text", "--pass-bg", "--label-card"], ["--review-text", "--review-bg", "--label-card"],
  ["--primary-foreground", "--cta-from"], ["--primary-foreground", "--cta-to"],
  ["--secondary-foreground", "--background"], ["--primary-strong", "--background"], ["--primary-strong", "--primary-soft", "--background"],
  ["--pass-text", "--pass-bg", "--background"], ["--review-text", "--review-bg", "--background"], ["--violation-text", "--violation-bg", "--background"],
];

describe("design token contrast (WCAG AA, 4.5:1 for body text)", () => {
  for (const [theme, tokens] of Object.entries(themes)) {
    it.each(PAIRS)(`${theme}: %s on %s`, (text, background, base) => {
      expect(ratio(tokens, text, background, base)).toBeGreaterThanOrEqual(4.5);
    });
  }

  it("defines every colour token in both themes", () => {
    const dark = block(':root[data-theme="dark"]');
    for (const name of ["--background", "--surface", "--surface-raised", "--surface-muted", "--muted", "--border", "--foreground", "--muted-foreground", "--primary", "--primary-strong", "--primary-soft", "--primary-border", "--violation-text", "--violation-bg", "--violation-border", "--review-text", "--review-bg", "--review-border", "--pass-text", "--pass-bg", "--pass-border", "--story-bg", "--label-card", "--grid-minor", "--grid-major", "--ruler-bg", "--ruler-tick", "--brand-mark-shadow", "--cta-glow", "--app-glow", "--scan-glow"]) {
      expect(light[name], `light ${name}`).toBeTruthy();
      expect(dark[name], `dark ${name}`).toBeTruthy();
    }
  });
});
