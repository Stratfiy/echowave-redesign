/**
 * Simple mode (launch stream care): applied from the person's own saved
 * preference, switchable back, and nothing at all while it is not offered.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.hoisted(() => vi.fn());
const put = vi.hoisted(() => vi.fn());
const flags = vi.hoisted(() => ({} as Record<string, boolean>));
vi.mock("@/client/sdk.gen", () => ({
    myPreferencesApiV1MePreferencesGet: get,
    saveMyPreferencesApiV1MePreferencesPut: put,
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => Boolean(flags[name]) }));

import { SimpleModeProvider, useSimpleMode } from "../simpleMode";

let state: ReturnType<typeof useSimpleMode>;
function Probe() {
    state = useSimpleMode();
    return <span data-testid="probe">{state.offered ? (state.on ? "on" : "off") : "not offered"}</span>;
}

beforeEach(() => {
    for (const key of Object.keys(flags)) delete flags[key];
    get.mockReset();
    put.mockReset();
    localStorage.clear();
    document.documentElement.removeAttribute("data-simple-mode");
});

describe("Simple mode", () => {
    it("does nothing at all while it is not offered", async () => {
        flags.member_preferences = true;
        render(
            <SimpleModeProvider>
                <Probe />
            </SimpleModeProvider>,
        );
        expect(screen.getByTestId("probe").textContent).toBe("not offered");
        expect(get).not.toHaveBeenCalled();
        expect(document.documentElement.hasAttribute("data-simple-mode")).toBe(false);
    });

    it("applies the saved preference and switches back", async () => {
        flags.member_preferences = true;
        flags.care_simple_mode = true;
        get.mockResolvedValue({ data: { simple_mode: true, revision: 3 } });
        put.mockResolvedValue({ data: { simple_mode: false, revision: 4 } });
        render(
            <SimpleModeProvider>
                <Probe />
            </SimpleModeProvider>,
        );
        await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("on"));
        expect(document.documentElement.getAttribute("data-simple-mode")).toBe("on");
        await act(async () => {
            await state.setOn(false);
        });
        expect(put).toHaveBeenCalledWith({ body: { simple_mode: false, revision: 3 } });
        expect(screen.getByTestId("probe").textContent).toBe("off");
        expect(document.documentElement.hasAttribute("data-simple-mode")).toBe(false);
    });

    it("saves over a change made in another tab rather than failing", async () => {
        flags.member_preferences = true;
        flags.care_simple_mode = true;
        get.mockResolvedValueOnce({ data: { simple_mode: false, revision: 1 } }).mockResolvedValue({ data: { simple_mode: false, revision: 2 } });
        put.mockResolvedValueOnce({ error: { detail: "conflict" }, response: { status: 409 } }).mockResolvedValue({ data: { simple_mode: true, revision: 3 } });
        render(
            <SimpleModeProvider>
                <Probe />
            </SimpleModeProvider>,
        );
        await waitFor(() => expect(get).toHaveBeenCalled());
        await act(async () => {
            await state.setOn(true);
        });
        expect(put).toHaveBeenLastCalledWith({ body: { simple_mode: true, revision: 2 } });
        expect(document.documentElement.getAttribute("data-simple-mode")).toBe("on");
    });
});
