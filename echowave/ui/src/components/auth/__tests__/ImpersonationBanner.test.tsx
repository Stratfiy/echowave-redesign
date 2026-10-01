import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ImpersonationBanner, recordImpersonationStop } from "../ImpersonationBanner";

const api = vi.hoisted(() => ({ stop: vi.fn() }));
vi.mock("@/client/sdk.gen", () => ({
  stopImpersonationApiV1ImpersonationStopPost: api.stop,
}));

afterEach(() => {
  document.cookie = "decibyl-impersonating=; Max-Age=0; Path=/";
  api.stop.mockReset();
  vi.restoreAllMocks();
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

  // ADMIN-2 (A4): the stop is audited before the session is cleared.
  it("records the stop, then submits to the route that clears the session", async () => {
    document.cookie = "decibyl-impersonating=owner%40clinic.example; Path=/";
    api.stop.mockResolvedValue({ data: { recorded: true } });
    const submit = vi
      .spyOn(HTMLFormElement.prototype, "submit")
      .mockImplementation(() => {});
    render(<ImpersonationBanner />);

    fireEvent.click(screen.getByRole("button", { name: /stop impersonating/i }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(api.stop).toHaveBeenCalledTimes(1);
  });

  it("still lets the staffer out when the audit call fails", async () => {
    document.cookie = "decibyl-impersonating=1; Path=/";
    api.stop.mockRejectedValue(new Error("network"));
    const submit = vi
      .spyOn(HTMLFormElement.prototype, "submit")
      .mockImplementation(() => {});
    render(<ImpersonationBanner />);
    fireEvent.click(screen.getByRole("button", { name: /stop impersonating/i }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
  });

  it("never waits on the audit longer than its timeout", async () => {
    api.stop.mockReturnValue(new Promise(() => {}));
    expect(await recordImpersonationStop(10)).toBe(false);
    api.stop.mockResolvedValue({ error: { detail: "x" } });
    expect(await recordImpersonationStop(10)).toBe(false);
    api.stop.mockResolvedValue({ data: { recorded: true } });
    expect(await recordImpersonationStop(10)).toBe(true);
  });
});
