import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

function rgb(hex: string) {
  const value = Number.parseInt(hex.slice(1), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

function luminance(hex: string) {
  const channels = rgb(hex).map((channel) => {
    const value = channel / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

function contrast(a: string, b: string) {
  const [lighter, darker] = [luminance(a), luminance(b)].sort((left, right) => right - left);
  return (lighter + 0.05) / (darker + 0.05);
}

function tokens(block: string) {
  return Object.fromEntries(
    [...block.matchAll(/--([a-z-]+):\s*(#[0-9a-f]{6})/gi)].map((match) => [match[1], match[2]]),
  );
}

describe("workstation colour tokens", () => {
  const css = readFileSync(new URL("./theme.css", import.meta.url), "utf8");
  const light = tokens(css.match(/:root,[\s\S]*?\n}/)?.[0] ?? "");
  const dark = tokens(css.match(/\[data-theme="dark"\][\s\S]*?\n}/)?.[0] ?? "");

  for (const [name, palette] of [["light", light], ["dark", dark]] as const) {
    it(`${name} text and semantic colours meet AA`, () => {
      for (const token of ["text", "text-soft", "action", "running", "success", "danger"]) {
        expect(contrast(palette[token], palette.surface), token).toBeGreaterThanOrEqual(4.5);
      }
    });

    it(`${name} control boundaries meet non-text contrast`, () => {
      expect(contrast(palette["rule-strong"], palette.control)).toBeGreaterThanOrEqual(3);
    });
  }
});
