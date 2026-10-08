import { describe, expect, it, vi } from "vitest";

import { definitelyNotStaff, isStaffPath } from "../staffGate";

const answer = (status: number, body: unknown) =>
    vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

describe("the staff area's middleware gate", () => {
    it("covers the staff area and nothing else", () => {
        expect(isStaffPath("/superadmin")).toBe(true);
        expect(isStaffPath("/superadmin/users/2")).toBe(true);
        expect(isStaffPath("/superadminx")).toBe(false);
        expect(isStaffPath("/overview")).toBe(false);
    });

    it("sends an owner or member home", async () => {
        expect(await definitelyNotStaff("http://api", "t", answer(200, { staff_role: null }))).toBe(true);
        expect(await definitelyNotStaff("http://api", "t", answer(200, {}))).toBe(true);
    });

    it("lets staff through", async () => {
        expect(await definitelyNotStaff("http://api", "t", answer(200, { staff_role: "superadmin" }))).toBe(false);
        expect(await definitelyNotStaff("http://api", "t", answer(200, { staff_role: "support" }))).toBe(false);
    });

    it("never bounces anyone on an uncertain answer", async () => {
        expect(await definitelyNotStaff("http://api", "t", answer(500, {}))).toBe(false);
        expect(await definitelyNotStaff("http://api", "t", answer(401, {}))).toBe(false);
        const down = vi.fn(async () => {
            throw new Error("down");
        }) as unknown as typeof fetch;
        expect(await definitelyNotStaff("http://api", "t", down)).toBe(false);
    });
});
