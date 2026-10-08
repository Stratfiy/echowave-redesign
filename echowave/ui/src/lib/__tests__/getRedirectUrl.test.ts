import { beforeEach, describe, expect, it, vi } from "vitest";

import { getRedirectUrl } from "../utils";

const api = vi.hoisted(() => ({ me: vi.fn(), count: vi.fn(), landing: vi.fn() }));
vi.mock("@/client/sdk.gen", () => ({
    getAuthUserApiV1UserAuthUserGet: api.me,
    getWorkflowCountApiV1WorkflowCountGet: api.count,
    impersonateApiV1SuperuserImpersonatePost: vi.fn(),
    landingApiV1ShellLandingGet: api.landing,
}));

beforeEach(() => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    api.count.mockReset();
    api.me.mockResolvedValue({ data: { id: 1, staff_role: null } });
    api.count.mockResolvedValue({ data: { total: 0, active: 0 } });
    api.landing.mockResolvedValue({ data: { path: null } });
});

describe("getRedirectUrl", () => {
    it("sends a new person to the build-an-agent journey while the flag is off (the old rule)", async () => {
        expect(await getRedirectUrl("tok", [{ id: "admin" }])).toBe("/start");
    });

    it("lands a new person in onboarding, then Chat, with the flag on -- never /start", async () => {
        api.landing.mockResolvedValue({ data: { path: "/welcome" } });
        expect(await getRedirectUrl("tok", [{ id: "admin" }])).toBe("/welcome");
        api.landing.mockResolvedValue({ data: { path: "/overview" } });
        expect(await getRedirectUrl("tok", [])).toBe("/overview");
        expect(api.count).not.toHaveBeenCalled();
    });

    it("still sends staff to the console first", async () => {
        api.me.mockResolvedValue({ data: { id: 1, staff_role: "superadmin" } });
        api.landing.mockResolvedValue({ data: { path: "/welcome" } });
        expect(await getRedirectUrl("tok", [])).toBe("/superadmin");
    });
});
