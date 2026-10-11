import { render, screen, within } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import type { Loaded } from "@/lib/staff/data";

const bucket = {
    calls: 4,
    input_tokens: 400,
    cache_read_tokens: 4000,
    cache_write_tokens: 2000,
    output_tokens: 80,
    reasoning_tokens: 0,
    cacheable_input_tokens: 8400,
    hit_rate: 0.4762,
    raw_hit_rate: 0.4762,
    warm_calls: 2,
    cold_calls: 2,
    cost_paise: 1234,
    unpriced: [],
};

const REPORT = {
    window: { start: "2026-10-02T00:00:00Z", end: "2026-10-09T00:00:00Z", days: 7 },
    totals: { ...bucket, conversations: 2, retry_calls: 1, side_calls: 1 },
    by_feature: [{ ...bucket, feature: "agent_call" }],
    by_model: [{ ...bucket, provider: "anthropic", model: "m" }],
    top_fingerprints: [{ ...bucket, prompt_fingerprint: "abcd1234abcd1234", feature: "agent_call" }],
    cold_warm: [{ ...bucket, feature: "agent_call", position: "first" }],
    cost_per_outcome: [
        { feature: "agent_call", tasks: 2, tasks_with_outcome: 2, succeeded: 1, cost_paise_of_tasks_with_outcome: 1234, cost_paise_per_success: 1234, retry_calls: 1, side_calls: 1 },
        { feature: "decibyl", tasks: 3, tasks_with_outcome: 0, succeeded: 0, cost_paise_of_tasks_with_outcome: 0, cost_paise_per_success: null, retry_calls: 0, side_calls: 0 },
    ],
    prefix_breaks: {
        by_feature: [{ feature: "agent_call", conversations: 2, conversations_with_breaks: 1, breaks: 1, system: 1, tools: 0, both: 0 }],
        top_conversations: [{ conversation_key: "run:2", feature: "agent_call", breaks: 1, kinds: { system: 1 } }],
        volatile_prefixes: [{ prompt_fingerprint: "fp", feature: "agent_call", conversations: 2, distinct_first_prefixes: 2 }],
    },
    capabilities: [
        { provider: "anthropic", model: "", supported: true, mechanism: "explicit_cache_control", min_cacheable_tokens: 1024, platform_requests_cache: true, cache_read_multiplier: 0.1, cache_write_multiplier: 1.25 },
        { provider: "sarvam", model: "", supported: "unknown", mechanism: "unknown", min_cacheable_tokens: "unknown", platform_requests_cache: "unknown", cache_read_multiplier: "unknown", cache_write_multiplier: "unknown" },
        { provider: "anthropic", model: "a-model", supported: true, mechanism: "explicit_cache_control", min_cacheable_tokens: 1024, platform_requests_cache: true, cache_read_multiplier: 0.1, cache_write_multiplier: 1.25 },
    ],
};

const seen: string[] = [];

vi.mock("@/lib/staff/data", () => ({
    useStaffData: (url: string | null, query?: Record<string, unknown>): Loaded<unknown> => {
        seen.push(`${url}?days=${String(query?.days)}`);
        return { state: "ok", data: REPORT, error: null, refreshedAt: new Date(), refresh: async () => {} };
    },
}));
vi.mock("@/components/staff/StaffShell", () => ({
    useReportFreshness: () => {},
}));

import CachingPage from "../page";

describe("the prompt caching page", () => {
    it("reads seven days from the staff route", () => {
        render(<CachingPage />);
        expect(seen).toContain("/api/v1/admin/staff/operations/caching?days=7");
    });

    it("shows hit rate, cost per success, prefix breakers and unknowns as unknown", () => {
        render(<CachingPage />);
        expect(screen.getAllByText("47.6%").length).toBeGreaterThan(0);
        expect(screen.getByText("run:2")).toBeTruthy();
        expect(screen.getByText(/something per-call sits in the prefix/)).toBeTruthy();
        const capabilities = screen.getByRole("region", { name: "What each vendor's cache does" });
        // One row per vendor, never per model, and an unknown says so.
        expect(within(capabilities).getAllByText("anthropic")).toHaveLength(1);
        expect(within(capabilities).getAllByText("unknown").length).toBeGreaterThan(0);
        // A chat has no outcome: an em dash, not zero.
        const outcomes = screen.getByRole("region", { name: "Cost per successful outcome" });
        expect(within(outcomes).getAllByText("—").length).toBeGreaterThan(0);
    });
});
