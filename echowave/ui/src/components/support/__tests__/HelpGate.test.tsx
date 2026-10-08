import { render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
    app: { config: { features: {} as Record<string, boolean> }, loading: false },
    org: { loading: false, orgFeatures: {} as Record<string, boolean> | null },
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/context/AppConfigContext", () => ({ useAppConfig: () => state.app }));
vi.mock("@/context/OrgConfigContext", () => ({ useOrgConfig: () => state.org }));
vi.mock("@/components/SpinLoader", () => ({ default: () => <p>spinner</p> }));

import { HelpGate } from "../HelpGate";

beforeEach(() => {
    state.app = { config: { features: {} }, loading: false };
    state.org = { loading: false, orgFeatures: {} };
});

describe("Help behind its switch", () => {
    it("is not available while off, as before Help existed", () => {
        render(<HelpGate>help</HelpGate>);
        expect(screen.getByText("This page is not available.")).toBeTruthy();
        expect(screen.queryByText("help")).toBeNull();
    });

    it("shows nothing until the switch is known", () => {
        state.org = { loading: true, orgFeatures: null };
        render(<HelpGate>help</HelpGate>);
        expect(screen.getByText("spinner")).toBeTruthy();
    });

    it("opens when on for the workspace", () => {
        state.org = { loading: false, orgFeatures: { support_help: true } };
        render(<HelpGate>help</HelpGate>);
        expect(screen.getByText("help")).toBeTruthy();
    });
});
