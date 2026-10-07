import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { isActive, SuperadminNav } from "../SuperadminNav";

const roles = vi.hoisted(() => ({
    value: { staffRole: "superadmin" as string | null, isStaff: true, loaded: true },
}));
const route = vi.hoisted(() => ({ pathname: "/superadmin/audit" }));
const flags = vi.hoisted(() => ({ support_inbox: false }));

vi.mock("@/hooks/useAccessRoles", () => ({ useAccessRoles: () => roles.value }));
vi.mock("next/navigation", () => ({ usePathname: () => route.pathname }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => Boolean((flags as Record<string, boolean>)[name]) }));
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
    flags.support_inbox = false;
});

describe("SuperadminNav", () => {
    it("links the new screens and marks where you are", () => {
        render(<SuperadminNav />);
        const audit = screen.getByRole("link", { name: "Audit log" });
        expect(audit.getAttribute("aria-current")).toBe("page");
        expect(screen.getByRole("link", { name: "System" }).getAttribute("href")).toBe("/superadmin/system");
        expect(screen.getByRole("link", { name: "Accounts" })).toBeTruthy();
    });

    it("links the support inbox only while it is switched on", () => {
        const { unmount } = render(<SuperadminNav />);
        expect(screen.queryByRole("link", { name: "Support" })).toBeNull();
        unmount();
        flags.support_inbox = true;
        route.pathname = "/superadmin/support/4";
        render(<SuperadminNav />);
        const support = screen.getByRole("link", { name: "Support" });
        expect(support.getAttribute("href")).toBe("/superadmin/support");
        expect(support.getAttribute("aria-current")).toBe("page");
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
