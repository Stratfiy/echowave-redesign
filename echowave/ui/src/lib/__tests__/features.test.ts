/** A switched-off feature is read from the flags /health already reported --
 * off until they arrive, and off for a name the backend did not report. */
import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const app = vi.hoisted(() => ({ config: null as null | { features: Record<string, boolean> } }));
vi.mock("@/context/AppConfigContext", () => ({ useAppConfig: () => ({ config: app.config }) }));

import { useFeature } from "../features";

beforeEach(() => {
    app.config = null;
});

describe("useFeature", () => {
    it("is off until the health check has answered", () => {
        expect(renderHook(() => useFeature("workspace_roles")).result.current).toBe(false);
    });

    it("follows the flag the backend reported", () => {
        app.config = { features: { workspace_roles: true, dialer_import: false } };
        expect(renderHook(() => useFeature("workspace_roles")).result.current).toBe(true);
        expect(renderHook(() => useFeature("dialer_import")).result.current).toBe(false);
        expect(renderHook(() => useFeature("task_board")).result.current).toBe(false);
    });
});
