import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AppLayout from "../AppLayout";

const state = vi.hoisted(() => ({ features: {} as Record<string, boolean> }));

vi.mock("next/navigation", () => ({ usePathname: () => "/overview", useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/context/AppConfigContext", () => ({
  useAppConfig: () => ({ config: { backendStatus: "reachable", features: state.features }, loading: false, refresh: vi.fn() }),
}));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => false }));
vi.mock("@/components/auth/ImpersonationBanner", () => ({ ImpersonationBanner: () => null }));
vi.mock("@/components/auth/VerifyEmailBanner", () => ({ VerifyEmailBanner: () => null }));
vi.mock("@/components/auth/AgreementsGate", () => ({ AgreementsGate: () => null }));
vi.mock("@/context/LeadFormsContext", () => ({ LeadFormsProvider: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("@/lib/themes", () => ({ applyTheme: vi.fn(), readStoredTheme: vi.fn() }));
vi.mock("../TopBar", () => ({ TopBar: () => null }));
vi.mock("../v2/AppRailV2", () => ({ AppRailV2: () => <nav aria-label="v2 rail" /> }));

afterEach(cleanup);
beforeEach(() => {
  state.features = {};
});

describe("the shell", () => {
  it("is the v2 rail and theme for everyone, whatever the old flag says", () => {
    const cases: Record<string, boolean>[] = [{}, { ui_shell_v2: false }, { ui_shell_v2: true }];
    for (const features of cases) {
      state.features = features;
      const { container, unmount } = render(<AppLayout>page</AppLayout>);
      expect(screen.getByRole("navigation", { name: "v2 rail" })).toBeTruthy();
      expect(container.querySelector(".shell-v2")).not.toBeNull();
      unmount();
    }
  });
});
