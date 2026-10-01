import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { systemLines, type SystemSnapshot, SystemStatusPanel, SystemStrip } from "../SystemStatus";

const api = vi.hoisted(() => ({ read: vi.fn() }));

vi.mock("@/client/sdk.gen", () => ({ readSystemStatusApiV1AdminSystemGet: api.read }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("next/link", () => ({
    default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
        <a href={href} {...rest}>
            {children}
        </a>
    ),
}));

function snapshot(over: Partial<SystemSnapshot> = {}): SystemSnapshot {
    return {
        status: "ok",
        build: { version: "1.42.0", git_sha: "abcdef1234567" },
        probe_timeout_seconds: 2,
        database: { ok: true, latency_ms: 4.2 },
        redis: { ok: true, latency_ms: 1.1 },
        queue: { ok: true, latency_ms: 1, length: 3 },
        worker: { ok: true, latency_ms: 2, alive: true, age_seconds: 20 },
        provider_balances: {
            ok: false,
            latency_ms: 900,
            needs_attention: 1,
            providers: [
                {
                    provider: "sarvam",
                    status: "low",
                    kind: "money",
                    remaining: 120,
                    currency: "INR",
                    needs_attention: true,
                    detail: null,
                },
            ],
        },
        ...over,
    };
}

beforeEach(() => api.read.mockReset());

describe("system lines", () => {
    it("reads each probe as a line", () => {
        const lines = systemLines(snapshot());
        const by = Object.fromEntries(lines.map((l) => [l.key, l]));
        expect(by.database.value).toBe("4 ms");
        expect(by.queue.value).toBe("3 waiting");
        expect(by.worker.value).toBe("Beat 20s ago");
        expect(by.balances.state).toBe("bad");
        expect(by.balances.value).toBe("1 need attention");
    });

    it("keeps 'no heartbeat' apart from 'stopped'", () => {
        const never = systemLines(snapshot({ worker: { ok: false, latency_ms: 1, alive: null } }));
        const stopped = systemLines(
            snapshot({ worker: { ok: false, latency_ms: 1, alive: false, age_seconds: 7200 } }),
        );
        expect(never.find((l) => l.key === "worker")?.state).toBe("unknown");
        expect(stopped.find((l) => l.key === "worker")?.value).toBe("Stopped · last beat 2h ago");
    });

    it("shows a probe that timed out as down, not blank", () => {
        const lines = systemLines(
            snapshot({ database: { ok: false, latency_ms: null, detail: "No answer within 2 s." } }),
        );
        const db = lines.find((l) => l.key === "database");
        expect(db?.value).toBe("Down");
        expect(db?.detail).toBe("No answer within 2 s.");
    });
});

describe("SystemStrip", () => {
    it("shows the build and links to the full page", async () => {
        api.read.mockResolvedValue({ data: snapshot() });
        render(<SystemStrip />);
        const link = await screen.findByRole("link", { name: "System status" });
        expect(link.getAttribute("href")).toBe("/superadmin/system");
        expect(link.textContent).toContain("v1.42.0 · abcdef1");
        expect(link.textContent).toContain("3 waiting");
    });

    it("says it could not read the status rather than vanishing", async () => {
        api.read.mockResolvedValue({ error: { detail: "Forbidden" }, response: { status: 403 } });
        render(<SystemStrip />);
        expect((await screen.findByRole("alert")).textContent).toContain("Forbidden");
    });
});

describe("SystemStatusPanel", () => {
    it("lists provider balances with the one needing attention", async () => {
        api.read.mockResolvedValue({ data: snapshot({ status: "degraded" }) });
        render(<SystemStatusPanel />);
        expect(await screen.findByText("Something needs attention")).toBeTruthy();
        expect(screen.getByText("sarvam")).toBeTruthy();
        expect(screen.getByText(/Each check is cut off after 2 s/)).toBeTruthy();
    });
});
