import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { openEntryPoint, registerTalk, resetEntryPoints, useEntryPoint } from "../chatEntryPoints";

function Probe() {
    const talk = useEntryPoint("talk");
    return <span data-testid="probe">{talk.available ? "available" : "unavailable"}</span>;
}

afterEach(() => resetEntryPoints());

describe("chat entry points", () => {
    it("is unavailable until a stream registers a handler, and follows it", () => {
        render(<Probe />);
        expect(screen.getByTestId("probe").textContent).toBe("unavailable");
        const handler = vi.fn();
        let unregister = () => {};
        act(() => {
            unregister = registerTalk(handler);
        });
        expect(screen.getByTestId("probe").textContent).toBe("available");
        expect(openEntryPoint("talk", { threadId: "t", draft: "hi" })).toBe(true);
        expect(handler).toHaveBeenCalledWith({ threadId: "t", draft: "hi" });
        act(() => unregister());
        expect(screen.getByTestId("probe").textContent).toBe("unavailable");
    });

    it("does nothing, and says so, with no handler", () => {
        expect(openEntryPoint("meeting", { threadId: null, draft: "" })).toBe(false);
    });
});
