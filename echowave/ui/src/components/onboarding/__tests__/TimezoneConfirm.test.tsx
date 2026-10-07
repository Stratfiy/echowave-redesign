import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { describeTimezone, TimezoneConfirm } from "../TimezoneConfirm";

describe("TimezoneConfirm", () => {
    it("asks before treating the detected zone as confirmed", () => {
        const onChange = vi.fn();
        render(<TimezoneConfirm value="Asia/Kolkata" confirmed={false} onChange={onChange} />);
        expect(screen.getByText("Asia/Kolkata")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Yes, that is right" }));
        expect(onChange).toHaveBeenCalledWith("Asia/Kolkata", true);
    });

    it("unconfirms while a different zone is being chosen", () => {
        const onChange = vi.fn();
        render(<TimezoneConfirm value="Asia/Kolkata" confirmed onChange={onChange} />);
        fireEvent.click(screen.getByRole("button", { name: "Change" }));
        fireEvent.change(screen.getByLabelText("Timezone"), { target: { value: "Asia/Dubai" } });
        expect(onChange).toHaveBeenCalledWith("Asia/Dubai", false);
    });

    it("describes the offset and the clock so the person can check it", () => {
        expect(describeTimezone("Asia/Kolkata", new Date("2026-10-07T10:00:00Z"))).toMatch(/GMT\+5:30/);
    });
});
