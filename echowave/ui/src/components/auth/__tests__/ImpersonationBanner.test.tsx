import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ImpersonationBanner } from "../ImpersonationBanner";

afterEach(() => {
  document.cookie = "decibyl-impersonating=; Max-Age=0; Path=/";
});

describe("ImpersonationBanner", () => {
  it("renders nothing for an ordinary session", () => {
    const { container } = render(<ImpersonationBanner />);
    expect(container.firstChild).toBeNull();
  });

  it("names who is being impersonated and offers the way out", () => {
    document.cookie = "decibyl-impersonating=owner%40clinic.example; Path=/";
    render(<ImpersonationBanner />);
    expect(screen.getByRole("status").textContent).toContain(
      "owner@clinic.example",
    );
    const stop = screen.getByRole("button", { name: /stop impersonating/i });
    expect(stop.getAttribute("type")).toBe("submit");
    expect(stop.closest("form")?.getAttribute("action")).toBe(
      "/impersonate/stop",
    );
    expect(stop.closest("form")?.getAttribute("method")).toBe("POST");
  });

  it("says 'a customer' when the marker carries no name", () => {
    document.cookie = "decibyl-impersonating=1; Path=/";
    render(<ImpersonationBanner />);
    expect(screen.getByRole("status").textContent).toContain("a customer");
  });
});
