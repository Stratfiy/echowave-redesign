import { beforeEach, describe, expect, it, vi } from "vitest";

import { shellLanding } from "../landing";

const api = vi.hoisted(() => ({ landing: vi.fn() }));
vi.mock("@/client/sdk.gen", () => ({ landingApiV1ShellLandingGet: api.landing }));

beforeEach(() => api.landing.mockReset());

describe("shellLanding", () => {
    it("takes the server's answer for the two paths it may name", async () => {
        api.landing.mockResolvedValue({ data: { path: "/welcome" } });
        expect(await shellLanding("tok")).toBe("/welcome");
        expect(api.landing.mock.calls[0][0].headers.Authorization).toBe("Bearer tok");
        api.landing.mockResolvedValue({ data: { path: "/overview" } });
        expect(await shellLanding("tok")).toBe("/overview");
    });

    it("keeps the caller's own rule while off, on a failure, or for anything else", async () => {
        api.landing.mockResolvedValue({ data: { path: null } });
        expect(await shellLanding("tok")).toBeNull();
        api.landing.mockResolvedValue({ error: { detail: "x" } });
        expect(await shellLanding("tok")).toBeNull();
        api.landing.mockRejectedValue(new Error("network"));
        expect(await shellLanding("tok")).toBeNull();
        api.landing.mockResolvedValue({ data: { path: "/start" } });
        expect(await shellLanding("tok")).toBeNull();
        expect(await shellLanding("")).toBeNull();
    });
});
