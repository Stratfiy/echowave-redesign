import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { isActive, SuperadminNav } from "../SuperadminNav";

const roles = vi.hoisted(() => ({
    value: { staffRole: "superadmin" as string | null, isStaff: true, loaded: true },
}));
const route = vi.hoisted(() => ({ pathname: "/superadmin/audit" }));

vi.mock("@/hooks/useAccessRoles", () => ({ useAccessRoles: () => roles.value }));
vi.mock("next/navigation", () => ({ usePathname: () => route.pathname }));
vi.mock("next/link", () => ({
    default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
        <a href={href} {...rest}>
            {children}
        </a>
    ),
}));

beforeEach(() => {
    roles.value = { staffRole: "superadmin", isStaff: true, loaded: true };
    route.pathname = "/superadmin/audit";
});

describe("SuperadminNav", () => {
    it("links the new screens and marks where you are", () => {
        render(<SuperadminNav />);
        const audit = screen.getByRole("link", { name: "Audit log" });
        expect(audit.getAttribute("aria-current")).toBe("page");
        expect(screen.getByRole("link", { name: "System" }).getAttribute("href")).toBe("/superadmin/system");
        expect(screen.getByRole("link", { name: "Accounts" })).toBeTruthy();
    });

    it("is not shown to support staff", () => {
        roles.value = { staffRole: "support", isStaff: true, loaded: true };
        const { container } = render(<SuperadminNav />);
        expect(container.firstChild).toBeNull();
    });

    it("matches a section by prefix, the console only exactly", () => {
        expect(isActive("/superadmin/billing/accounts/5", "/superadmin/billing/accounts")).toBe(true);
        expect(isActive("/superadmin/audit", "/superadmin", true)).toBe(false);
    });
});
