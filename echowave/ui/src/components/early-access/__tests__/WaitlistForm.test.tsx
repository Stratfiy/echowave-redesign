import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { languageLabel, WaitlistForm } from "../WaitlistForm";

const api = vi.hoisted(() => ({ join: vi.fn(), languages: vi.fn() }));
vi.mock("@/client/sdk.gen", () => ({
    joinWaitlistApiV1PublicEarlyAccessWaitlistPost: api.join,
    earlyAccessLanguagesApiV1PublicEarlyAccessLanguagesGet: api.languages,
}));

beforeEach(() => {
    api.join.mockReset();
    api.languages.mockReset();
    api.languages.mockResolvedValue({
        data: [
            { code: "en", native: "English", english: "English", voice: true },
            { code: "ta", native: "தமிழ்", english: "Tamil", voice: true },
        ],
    });
});

function fill(email = "asha@example.in") {
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
}

describe("WaitlistForm", () => {
    it("offers languages by their own names first", async () => {
        render(<WaitlistForm />);
        expect(await screen.findByRole("option", { name: "தமிழ் · Tamil" })).toBeTruthy();
        expect(languageLabel({ code: "en", native: "English", english: "English", voice: true })).toBe("English");
    });

    it("refuses an invalid email without sending, keeping the form steady", async () => {
        render(<WaitlistForm />);
        fill("not-an-email");
        fireEvent.click(screen.getByRole("button", { name: "Join the waitlist" }));
        expect(await screen.findByText(/Enter an email address/)).toBeTruthy();
        expect(api.join).not.toHaveBeenCalled();
    });

    it("submits once, even when pressed twice", async () => {
        let finish: (value: unknown) => void = () => {};
        api.join.mockReturnValue(new Promise((resolve) => (finish = resolve)));
        render(<WaitlistForm />);
        fill();
        const button = screen.getByRole("button", { name: "Join the waitlist" });
        fireEvent.click(button);
        fireEvent.click(button);
        fireEvent.submit(screen.getByTestId("waitlist-form"));
        expect(api.join).toHaveBeenCalledTimes(1);
        expect((screen.getByRole("button", { name: "Sending…" }) as HTMLButtonElement).disabled).toBe(true);
        finish({ data: { state: "waitlisted", created: true } });
        expect(await screen.findByText("You are on the list")).toBeTruthy();
    });

    it("sends what was typed, in the chosen language", async () => {
        api.join.mockResolvedValue({ data: { state: "waitlisted", created: true } });
        render(<WaitlistForm />);
        await screen.findByRole("option", { name: "தமிழ் · Tamil" });
        fill(" asha@example.in ");
        fireEvent.change(screen.getByLabelText("Language"), { target: { value: "ta" } });
        fireEvent.change(screen.getByLabelText(/What would you ask/), { target: { value: "Plan my week" } });
        fireEvent.click(screen.getByRole("button", { name: "Join the waitlist" }));
        await waitFor(() => expect(api.join).toHaveBeenCalled());
        expect(api.join.mock.calls[0][0].body).toMatchObject({
            email: "asha@example.in",
            language: "ta",
            first_task: "Plan my week",
            phone: null,
            renewal: false,
        });
    });

    it("keeps every value after a failure", async () => {
        api.join.mockResolvedValue({ error: { detail: "Too many tries from here." } });
        render(<WaitlistForm />);
        fill();
        fireEvent.change(screen.getByLabelText(/What would you ask/), { target: { value: "Plan my week" } });
        fireEvent.click(screen.getByRole("button", { name: "Join the waitlist" }));
        expect(await screen.findByRole("alert")).toBeTruthy();
        expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe("asha@example.in");
        expect((screen.getByLabelText(/What would you ask/) as HTMLTextAreaElement).value).toBe("Plan my week");
    });

    it.each([
        [{ state: "waitlisted", created: false }, "You are already on the list"],
        [{ state: "already_registered", created: false }, "This address already has an account"],
        [{ state: "invited", created: false }, "Your invitation is waiting"],
    ])("says each server state plainly: %o", async (data, title) => {
        api.join.mockResolvedValue({ data });
        render(<WaitlistForm />);
        fill();
        fireEvent.click(screen.getByRole("button", { name: "Join the waitlist" }));
        expect(await screen.findByText(title)).toBeTruthy();
    });

    it("marks a request from a dead invitation as a renewal", async () => {
        api.join.mockResolvedValue({ data: { state: "waitlisted", created: true } });
        render(<WaitlistForm renewal />);
        fill();
        fireEvent.click(screen.getByRole("button", { name: "Join the waitlist" }));
        await waitFor(() => expect(api.join.mock.calls[0][0].body.renewal).toBe(true));
    });

    it("uses 16px inputs and 44px actions", () => {
        render(<WaitlistForm />);
        expect(screen.getByLabelText("Email").className).toContain("text-base");
        expect(screen.getByRole("button", { name: "Join the waitlist" }).className).toContain("min-h-11");
    });
});
