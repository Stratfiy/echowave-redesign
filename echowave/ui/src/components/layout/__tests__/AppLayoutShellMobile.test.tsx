import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AppLayout from "../AppLayout";

const state = vi.hoisted(() => ({ features: {} as Record<string, boolean>, pathname: "/overview" }));

vi.mock("next/navigation", () => ({ usePathname: () => state.pathname, useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/context/AppConfigContext", () => ({
    useAppConfig: () => ({ config: { backendStatus: "reachable", features: state.features }, loading: false, refresh: vi.fn() }),
}));
vi.mock("@/context/OrgConfigContext", () => ({ useOrgFeatures: () => ({}) }));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => true }));
vi.mock("@/components/auth/ImpersonationBanner", () => ({ ImpersonationBanner: () => null }));
vi.mock("@/components/auth/VerifyEmailBanner", () => ({ VerifyEmailBanner: () => null }));
vi.mock("@/components/auth/AgreementsGate", () => ({ AgreementsGate: () => null }));
vi.mock("@/context/LeadFormsContext", () => ({ LeadFormsProvider: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("@/lib/themes", () => ({ applyTheme: vi.fn(), readStoredTheme: vi.fn() }));
vi.mock("../TopBar", () => ({ TopBar: () => <div data-testid="top-bar" /> }));
vi.mock("../v2/AppRailV2", () => ({ AppRailV2: () => <nav aria-label="v2 rail" /> }));
vi.mock("../v2/MobileHeader", () => ({ MobileHeader: () => <header data-testid="mobile-header" /> }));
vi.mock("../v2/MobileTabBar", () => ({ MobileTabBar: () => <nav data-testid="tab-bar" /> }));

afterEach(cleanup);
beforeEach(() => {
    state.features = {};
    state.pathname = "/overview";
});

describe("the shell on a phone", () => {
    it("adds the phone header only with shell_mobile, and keeps the desktop bar for desktop", () => {
        render(<AppLayout>page</AppLayout>);
        expect(screen.queryByTestId("mobile-header")).toBeNull();
        expect(screen.getByTestId("top-bar").parentElement?.className ?? "").not.toContain("hidden");
        cleanup();
        state.features = { shell_mobile: true };
        render(<AppLayout>page</AppLayout>);
        expect(screen.getByTestId("mobile-header")).toBeTruthy();
        expect(screen.getByTestId("top-bar").parentElement?.className).toContain("md:block");
    });

    it("keeps the bottom bar on the workflow editor, where a phone sees the step list", () => {
        state.pathname = "/workflow/12";
        render(<AppLayout>page</AppLayout>);
        expect(screen.queryByTestId("tab-bar")).toBeNull();
        cleanup();
        state.features = { shell_mobile: true };
        render(<AppLayout>page</AppLayout>);
        expect(screen.getByTestId("tab-bar")).toBeTruthy();
    });

    it("draws no rail on the door's pages", () => {
        for (const path of ["/early-access", "/invite/ABCD-2345", "/welcome"]) {
            state.pathname = path;
            render(<AppLayout>page</AppLayout>);
            expect(screen.queryByRole("navigation", { name: "v2 rail" }), path).toBeNull();
            cleanup();
        }
    });
});
