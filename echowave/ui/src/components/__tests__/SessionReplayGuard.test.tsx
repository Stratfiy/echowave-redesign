import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ pathname: "/overview", enabled: false, recording: false }));

vi.mock("next/navigation", () => ({ usePathname: () => state.pathname }));
vi.mock("@/lib/features", () => ({ useFeature: () => state.enabled }));
vi.mock("posthog-js", () => ({
    default: {
        __loaded: true,
        startSessionRecording: vi.fn(() => {
            state.recording = true;
        }),
        stopSessionRecording: vi.fn(() => {
            state.recording = false;
        }),
        sessionRecordingStarted: () => state.recording,
    },
}));

import posthog from "posthog-js";

import SessionReplayGuard from "../SessionReplayGuard";

describe("SessionReplayGuard", () => {
    beforeEach(() => {
        state.recording = false;
        vi.mocked(posthog.startSessionRecording).mockClear();
        vi.mocked(posthog.stopSessionRecording).mockClear();
    });

    it("records nothing while the flag is off", () => {
        state.enabled = false;
        state.pathname = "/overview";
        render(<SessionReplayGuard />);
        expect(posthog.startSessionRecording).not.toHaveBeenCalled();
    });

    it("records an allowed screen and stops on a sensitive one", () => {
        state.enabled = true;
        state.pathname = "/overview";
        const view = render(<SessionReplayGuard />);
        expect(posthog.startSessionRecording).toHaveBeenCalledTimes(1);
        state.pathname = "/superadmin/provider-keys";
        view.rerender(<SessionReplayGuard />);
        expect(posthog.stopSessionRecording).toHaveBeenCalledTimes(1);
        expect(state.recording).toBe(false);
    });
});
