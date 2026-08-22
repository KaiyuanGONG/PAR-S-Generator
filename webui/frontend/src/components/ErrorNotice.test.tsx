// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { I18nProvider } from "../i18n";
import ErrorNotice from "./ErrorNotice";

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

afterEach(cleanup);

describe("ErrorNotice", () => {
  it("localizes actionable HTTP errors and preserves raw diagnostics", () => {
    render(<I18nProvider initialLocale="zh" storage={null}><ErrorNotice error={new ApiError(409, "task run-7 is already active")} /></I18nProvider>);
    expect(screen.getByRole("alert")).toHaveTextContent("该 run 与既有或活动 run 冲突");
    expect(screen.getByText("服务端详情")).toBeInTheDocument();
    expect(screen.getByText("task run-7 is already active")).toBeInTheDocument();
  });

  it("shows an unknown error without inventing guidance", () => {
    render(<I18nProvider initialLocale="en" storage={null}><ErrorNotice error={new Error("socket closed")} /></I18nProvider>);
    expect(screen.getByRole("alert")).toHaveTextContent("socket closed");
    expect(screen.queryByText("Service details")).not.toBeInTheDocument();
  });
});
