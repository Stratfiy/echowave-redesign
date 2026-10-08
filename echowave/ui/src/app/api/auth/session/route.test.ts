import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const jar = new Map<string, { value: string; maxAge?: number }>();
vi.mock("next/headers", () => ({
    cookies: async () => ({
        set: (name: string, value: string, options: { maxAge?: number }) => jar.set(name, { value, maxAge: options?.maxAge }),
        get: (name: string) => (jar.has(name) ? { name, value: jar.get(name)!.value } : undefined),
    }),
}));

import { POST as logout } from "../logout/route";
import { POST as signIn } from "./route";

describe("signing in or out as yourself ends any borrowed-session banner", () => {
    beforeEach(() => jar.clear());

    it("drops the impersonation marker on sign-in", async () => {
        await signIn(
            new NextRequest("http://localhost:3000/api/auth/session", {
                method: "POST",
                body: JSON.stringify({ token: "t", user: { id: "1" } }),
            }),
        );
        expect(jar.get("decibyl_auth_token")?.value).toBe("t");
        expect(jar.get("decibyl-impersonating")).toEqual({ value: "", maxAge: 0 });
    });

    it("drops it on sign-out", async () => {
        await logout();
        expect(jar.get("decibyl-impersonating")).toEqual({ value: "", maxAge: 0 });
    });
});
