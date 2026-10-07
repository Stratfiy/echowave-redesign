import { render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const flags = vi.hoisted(() => ({ on: false, loading: false }));

vi.mock("@/context/AppConfigContext", () => ({ useAppConfig: () => ({ config: {}, loading: flags.loading }) }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => name === "staff_console" && flags.on }));
vi.mock("../SuperadminGate", () => ({ SuperadminGate: ({ children }: { children: React.ReactNode }) => <div data-testid="old-gate">{children}</div> }));
vi.mock("../SuperadminNav", () => ({ SuperadminNav: () => <nav data-testid="old-nav" /> }));
vi.mock("@/components/staff/StaffShell", () => ({ StaffShell: ({ children }: { children: React.ReactNode }) => <div data-testid="staff-shell">{children}</div> }));

import { SuperadminChrome } from "../SuperadminChrome";

beforeEach(() => {
    flags.on = false;
    flags.loading = false;
});

describe("SuperadminChrome", () => {
    it("is exactly today's /superadmin while staff_console is off", () => {
        render(<SuperadminChrome>page</SuperadminChrome>);
        expect(screen.getByTestId("old-gate")).toBeTruthy();
        expect(screen.getByTestId("old-nav")).toBeTruthy();
        expect(screen.queryByTestId("staff-shell")).toBeNull();
    });

    it("is the staff console when it is on, without the old strip", () => {
        flags.on = true;
        render(<SuperadminChrome>page</SuperadminChrome>);
        expect(screen.getByTestId("staff-shell").textContent).toBe("page");
        expect(screen.queryByTestId("old-nav")).toBeNull();
    });

    it("draws neither until the flags are known", () => {
        flags.loading = true;
        const { container } = render(<SuperadminChrome>page</SuperadminChrome>);
        expect(container.firstChild).toBeNull();
    });
});
