import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TrialView } from "../orgHealth";
import { endOfDayIST, TrialControls } from "../TrialControls";

const api = vi.hoisted(() => ({
    setTrial: vi.fn(),
    confirm: vi.fn(),
}));

vi.mock("@/client/sdk.gen", () => ({
    setTrialEndApiV1SuperuserOrganizationsOrganizationIdTrialPost: api.setTrial,
}));
vi.mock("@/components/ConfirmDialog", () => ({
    useConfirm: () => ({ confirm: api.confirm, dialog: null }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const onTrial: TrialView = {
    on_trial: true,
    active: true,
    stage: "active",
    notice_stage: null,
    starts_at: "2026-10-04T00:00:00+05:30",
    ends_at: "2026-10-18T00:00:00+05:30",
    days_left: 9,
    override: false,
};

beforeEach(() => {
    api.setTrial.mockReset();
    api.confirm.mockReset();
    api.setTrial.mockResolvedValue({
        data: { organization_id: 5, trial: { ...onTrial, ends_at: "2026-10-25T10:00:00Z" } },
    });
});

describe("TrialControls", () => {
    it("extends by 7 days after asking, and shows the new end", async () => {
        api.confirm.mockResolvedValue(true);
        const onChanged = vi.fn();
        render(<TrialControls organizationId={5} trial={onTrial} onChanged={onChanged} />);

        fireEvent.click(screen.getByRole("button", { name: /extend trial by 7 days/i }));

        await waitFor(() => expect(api.setTrial).toHaveBeenCalledTimes(1));
        const call = api.setTrial.mock.calls[0][0];
        expect(call.path).toEqual({ organization_id: 5 });
        expect(call.body.extend_days).toBe(7);
        await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Trial now ends"));
        expect(onChanged).toHaveBeenCalled();
    });

    it("does nothing when the confirmation is declined", async () => {
        api.confirm.mockResolvedValue(false);
        render(<TrialControls organizationId={5} trial={onTrial} onChanged={vi.fn()} />);
        fireEvent.click(screen.getByRole("button", { name: /end trial now/i }));
        await waitFor(() => expect(api.confirm).toHaveBeenCalled());
        expect(api.setTrial).not.toHaveBeenCalled();
    });

    it("sets a chosen end date at the close of that day in India", async () => {
        api.confirm.mockResolvedValue(true);
        render(<TrialControls organizationId={5} trial={onTrial} onChanged={vi.fn()} />);
        fireEvent.change(screen.getByLabelText("New end date"), { target: { value: "2026-11-10" } });
        fireEvent.click(screen.getByRole("button", { name: /^set end date$/i }));
        await waitFor(() => expect(api.setTrial).toHaveBeenCalled());
        expect(api.setTrial.mock.calls[0][0].body.ends_at).toBe("2026-11-10T23:59:59+05:30");
        expect(endOfDayIST("2026-01-02")).toBe("2026-01-02T23:59:59+05:30");
    });

    it("pauses by ending the trial now", async () => {
        api.confirm.mockResolvedValue(true);
        render(<TrialControls organizationId={5} trial={onTrial} onChanged={vi.fn()} />);
        fireEvent.click(screen.getByRole("button", { name: /pause account/i }));
        await waitFor(() => expect(api.setTrial).toHaveBeenCalled());
        const body = api.setTrial.mock.calls[0][0].body;
        expect(typeof body.ends_at).toBe("string");
        expect(body.note).toContain("Paused");
    });

    it("explains, and refuses, a pause for an account that is not on the trial", () => {
        render(
            <TrialControls
                organizationId={5}
                trial={{ ...onTrial, on_trial: false, active: false, stage: "not_on_trial" }}
                onChanged={vi.fn()}
            />,
        );
        const pause = screen.getByRole("button", { name: /pause account/i }) as HTMLButtonElement;
        expect(pause.disabled).toBe(true);
        expect(screen.getByText(/Pause works only for accounts on the trial/)).toBeTruthy();
    });

    it("shows the server's refusal instead of a success", async () => {
        api.confirm.mockResolvedValue(true);
        api.setTrial.mockResolvedValue({ error: { detail: "No such organization" }, response: { status: 404 } });
        render(<TrialControls organizationId={5} trial={onTrial} onChanged={vi.fn()} />);
        fireEvent.click(screen.getByRole("button", { name: /extend trial by 7 days/i }));
        await waitFor(() => expect(api.setTrial).toHaveBeenCalled());
        expect(screen.queryByRole("status")).toBeNull();
    });
});
