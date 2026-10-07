import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AppLayout from "../AppLayout";

const state = vi.hoisted(() => ({ features: {} as Record<string, boolean>, pathname: "/superadmin/overview" }));

vi.mock("next/navigation", () => ({ usePathname: () => state.pathname, useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/context/AppConfigContext", () => ({
    useAppConfig: () => ({ config: { backendStatus: "reachable", features: state.features }, loading: false, refresh: vi.fn() }),
}));
vi.mock("@/context/OrgConfigContext", () => ({ useOrgFeatures: () => ({}) }));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => false }));
vi.mock("@/components/auth/ImpersonationBanner", () => ({ ImpersonationBanner: () => null }));
vi.mock("@/components/auth/VerifyEmailBanner", () => ({ VerifyEmailBanner: () => null }));
vi.mock("@/components/auth/AgreementsGate", () => ({ AgreementsGate: () => null }));
vi.mock("@/context/LeadFormsContext", () => ({ LeadFormsProvider: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("@/lib/themes", () => ({ applyTheme: vi.fn(), readStoredTheme: vi.fn() }));
vi.mock("../TopBar", () => ({ TopBar: () => null }));
vi.mock("../v2/AppRailV2", () => ({ AppRailV2: () => <nav aria-label="customer rail" /> }));

afterEach(cleanup);
beforeEach(() => {
    state.features = {};
    state.pathname = "/superadmin/overview";
});

describe("the staff console and the customer rail", () => {
    it("keeps today's rail on /superadmin while staff_console is off", () => {
        render(<AppLayout>page</AppLayout>);
        expect(screen.getByRole("navigation", { name: "customer rail" })).toBeTruthy();
    });

    it("does not embed the customer rail in the console when it is on", () => {
        state.features = { staff_console: true };
        render(<AppLayout>page</AppLayout>);
        expect(screen.queryByRole("navigation", { name: "customer rail" })).toBeNull();
        expect(screen.getByText("page")).toBeTruthy();
    });

    it("leaves every other page's rail alone when it is on", () => {
        state.features = { staff_console: true };
        state.pathname = "/overview";
        render(<AppLayout>page</AppLayout>);
        expect(screen.getByRole("navigation", { name: "customer rail" })).toBeTruthy();
    });
});
