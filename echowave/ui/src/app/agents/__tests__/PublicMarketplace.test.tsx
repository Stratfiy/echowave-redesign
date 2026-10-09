import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { connectorsOf, factsOf, stepKindLabel } from "@/lib/publicMarketplace";

const pack = vi.hoisted(() => ({
    card: {
        slug: "outbound_prospecting",
        name: "Outbound Prospecting",
        summary: "Finds businesses that fit your ideal customer.",
        job: "Business development executive",
        publisher: { slug: "decibyl", name: "Decibyl", first_party: true },
        badges: ["Email", "Scheduled"],
        industries: [],
        languages: ["en"],
        demo_url: null,
    },
    template_id: "outbound_prospecting",
    flow: "standard",
    steps: [
        { key: "onboarding", title: "t", detail: "d", blocking: true, facts: [{ key: "offer", question: "What are you offering them?", kind: "text", required: true, example: "A free month", used_for: "The one sentence every email makes." }] },
        { key: "requirements", title: "t", detail: "d", blocking: false, connectors: [{ app: "gmail", label: "Gmail", used_for: "Sending each approved email.", required: false }] },
    ],
    guardrails: ["Never invent a number."],
    compliance_notes: ["CAN-SPAM"],
    outline: [
        { name: "Find prospects", kind: "start" },
        { name: "Draft one email each", kind: "step" },
        { name: "Close", kind: "finish" },
    ],
    speaks: false,
    runs: "every weekday morning",
}));

vi.mock("@/lib/publicMarketplace", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@/lib/publicMarketplace")>();
    return { ...actual, fetchPack: vi.fn(async () => pack) };
});
vi.mock("next/navigation", () => ({ notFound: () => { throw new Error("404"); } }));

describe("the public role page", () => {
    it("shows a role in full, in its channel's words", async () => {
        const { default: Page } = await import("../[slug]/page");
        render(await Page({ params: Promise.resolve({ slug: "outbound_prospecting" }) }));
        expect(screen.getByRole("heading", { name: "Outbound Prospecting" })).toBeTruthy();
        expect(screen.getByText("Find prospects")).toBeTruthy();
        expect(screen.getByText("Starts")).toBeTruthy();
        expect(screen.queryByText("Call opens")).toBeNull();
        expect(screen.getByText("Runs every weekday morning.")).toBeTruthy();
        expect(screen.getByText("What are you offering them?")).toBeTruthy();
        expect(screen.getByText("Gmail")).toBeTruthy();
        expect(screen.getByText("Never invent a number.")).toBeTruthy();
        expect(screen.getByRole("link", { name: /Add Outbound Prospecting/ }).getAttribute("href")).toBe("/auth/signup");
    });
});

describe("page words", () => {
    it("never calls a back-office role's work a call", () => {
        expect(stepKindLabel("start", false)).toBe("Starts");
        expect(stepKindLabel("finish", false)).toBe("Finishes");
        expect(stepKindLabel("start", true)).toBe("Call opens");
        expect(stepKindLabel("step", true)).toBe("Step");
    });
    it("gathers facts and apps across the hiring steps", () => {
        expect(factsOf(pack as never).map((f) => f.key)).toEqual(["offer"]);
        expect(connectorsOf(pack as never).map((c) => c.app)).toEqual(["gmail"]);
    });
});
