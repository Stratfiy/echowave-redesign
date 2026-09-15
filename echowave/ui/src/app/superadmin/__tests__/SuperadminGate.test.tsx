import { render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SuperadminGate } from "../SuperadminGate";

const roles = vi.hoisted(() => ({
    value: {
        staffRole: null as string | null,
        isStaff: false,
        loaded: true,
    },
}));
const route = vi.hoisted(() => ({ pathname: "/superadmin" }));

vi.mock("@/hooks/useAccessRoles", () => ({
    useAccessRoles: () => roles.value,
}));
vi.mock("next/navigation", () => ({
    usePathname: () => route.pathname,
}));

function child() {
    return <div data-testid="console">console</div>;
}

beforeEach(() => {
    roles.value = { staffRole: null, isStaff: false, loaded: true };
    route.pathname = "/superadmin";
});

describe("the superadmin client gate", () => {
    it("shows nothing until the server has answered", () => {
        roles.value = { staffRole: null, isStaff: false, loaded: false };
        const { container } = render(<SuperadminGate>{child()}</SuperadminGate>);
        expect(container.firstChild).toBeNull();
        expect(screen.queryByTestId("console")).toBeNull();
    });

    it("refuses a customer", () => {
        roles.value = { staffRole: null, isStaff: false, loaded: true };
        render(<SuperadminGate>{child()}</SuperadminGate>);
        expect(screen.queryByTestId("console")).toBeNull();
        expect(screen.getByText("This page is not available")).toBeTruthy();
    });

    it("refuses a support-tier staffer on the console", () => {
        roles.value = { staffRole: "support", isStaff: true, loaded: true };
        route.pathname = "/superadmin";
        render(<SuperadminGate>{child()}</SuperadminGate>);
        expect(screen.queryByTestId("console")).toBeNull();
        expect(screen.getByText("This page is not available")).toBeTruthy();
    });

    it("lets a support-tier staffer into the KYC review queue", () => {
        roles.value = { staffRole: "support", isStaff: true, loaded: true };
        route.pathname = "/superadmin/verification";
        render(<SuperadminGate>{child()}</SuperadminGate>);
        expect(screen.getByTestId("console")).toBeTruthy();
    });

    it("lets a superadmin into everything, including the console", () => {
        roles.value = { staffRole: "superadmin", isStaff: true, loaded: true };
        route.pathname = "/superadmin";
        render(<SuperadminGate>{child()}</SuperadminGate>);
        expect(screen.getByTestId("console")).toBeTruthy();
    });
});
