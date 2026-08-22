import { describe, expect, it } from "vitest";
import { detectLocale, detectThemePreference, translate } from "./i18n";

function storage(values: Record<string, string>) {
  return {
    getItem: (key: string) => values[key] ?? null,
    setItem: () => {},
  };
}

describe("localized workspace preferences", () => {
  it("restores explicit locale and theme choices", () => {
    const preferences = storage({ "pars.locale": "fr", "pars.theme": "dark" });
    expect(detectLocale(preferences)).toBe("fr");
    expect(detectThemePreference(preferences)).toBe("dark");
  });

  it("interpolates typed values in all supported locales", () => {
    expect(translate("en", "run.cases.count", { count: 3 })).toBe("3 cases recorded");
    expect(translate("zh", "phantom.preview.case", { caseId: "0003", seed: 44 })).toContain("种子 44");
    expect(translate("fr", "seal.hash", { hash: "a1b2" })).toBe("SHA-256 · a1b2");
  });
});
