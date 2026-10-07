import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatWithTask, FirstTaskOnboarding, preferredLanguage } from "../FirstTaskOnboarding";

const api = vi.hoisted(() => ({ get: vi.fn(), save: vi.fn(), skip: vi.fn() }));
const router = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("@/client/sdk.gen", () => ({
    getOnboardingApiV1ShellOnboardingGet: api.get,
    saveOnboardingApiV1ShellOnboardingPut: api.save,
    skipOnboardingApiV1ShellOnboardingSkipPost: api.skip,
}));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));

const languages = [
    { code: "en", native: "English", english: "English", voice: true },
    { code: "ta", native: "தமிழ்", english: "Tamil", voice: true },
    { code: "ur", native: "اردو", english: "Urdu", voice: false },
];

beforeEach(() => {
    api.get.mockReset();
    api.save.mockReset();
    api.skip.mockReset();
    router.replace.mockReset();
    api.get.mockResolvedValue({
        data: { enabled: true, completed: false, timezone_confirmed: false, language: null, timezone: null, languages },
    });
});

describe("the first-task onboarding", () => {
    it("lists languages by their native names, with a text-only note", async () => {
        render(<FirstTaskOnboarding />);
        expect(await screen.findByText("தமிழ்")).toBeTruthy();
        fireEvent.click(screen.getByRole("radio", { name: /Urdu/ }));
        expect(screen.getByText(/not available in Urdu yet/)).toBeTruthy();
    });

    it("finds a language by either name", async () => {
        render(<FirstTaskOnboarding />);
        await screen.findByText("தமிழ்");
        fireEvent.change(screen.getByLabelText("Search languages"), { target: { value: "tamil" } });
        expect(screen.getAllByRole("radio")).toHaveLength(1);
        fireEvent.change(screen.getByLabelText("Search languages"), { target: { value: "தமி" } });
        expect(screen.getByRole("radio", { name: /தமிழ்/ })).toBeTruthy();
    });

    it("will not continue on a timezone nobody confirmed", async () => {
        render(<FirstTaskOnboarding />);
        await screen.findByText("தமிழ்");
        fireEvent.click(screen.getByRole("button", { name: /Go to Chat/ }));
        expect(await screen.findByText("Confirm your timezone before continuing.")).toBeTruthy();
        expect(api.save).not.toHaveBeenCalled();
    });

    it("saves the person's answers once and opens Chat with the task", async () => {
        api.save.mockResolvedValue({ data: { completed: true } });
        render(<FirstTaskOnboarding />);
        await screen.findByText("தமிழ்");
        fireEvent.click(screen.getByRole("radio", { name: /Tamil/ }));
        fireEvent.click(screen.getByRole("button", { name: "Yes, that is right" }));
        fireEvent.change(screen.getByLabelText(/Ask for anything/), { target: { value: "Help me plan today" } });
        const start = screen.getByRole("button", { name: /Start in Chat/ });
        fireEvent.click(start);
        fireEvent.click(start);
        await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/overview?ask=Help%20me%20plan%20today"));
        expect(api.save).toHaveBeenCalledTimes(1);
        expect(api.save.mock.calls[0][0].body).toMatchObject({ language: "ta", timezone_confirmed: true, complete: true });
    });

    it("keeps everything and says so when the save fails", async () => {
        api.save.mockResolvedValue({ error: { detail: "Your change was not saved. Try again." } });
        render(<FirstTaskOnboarding />);
        await screen.findByText("தமிழ்");
        fireEvent.click(screen.getByRole("button", { name: "Yes, that is right" }));
        fireEvent.change(screen.getByLabelText(/Ask for anything/), { target: { value: "Plan" } });
        fireEvent.click(screen.getByRole("button", { name: /Start in Chat/ }));
        expect(await screen.findByRole("alert")).toBeTruthy();
        expect((screen.getByLabelText(/Ask for anything/) as HTMLTextAreaElement).value).toBe("Plan");
        expect(router.replace).not.toHaveBeenCalled();
    });

    it("lets the person skip straight to Chat", async () => {
        api.skip.mockResolvedValue({ data: { completed: true } });
        render(<FirstTaskOnboarding />);
        await screen.findByText("தமிழ்");
        fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
        await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/overview"));
    });

    it("hands over to Chat when already done or switched off", async () => {
        api.get.mockResolvedValueOnce({ data: { enabled: true, completed: true, languages } });
        const { unmount } = render(<FirstTaskOnboarding />);
        await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/overview"));
        unmount();
        router.replace.mockReset();
        api.get.mockResolvedValueOnce({ error: { detail: "Not Found" }, response: { status: 404 } });
        render(<FirstTaskOnboarding />);
        await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/overview"));
    });

    it("shows a retry when setup cannot load, never a blank form", async () => {
        api.get.mockResolvedValueOnce({ error: { detail: "boom" }, response: { status: 500 } });
        render(<FirstTaskOnboarding />);
        expect(await screen.findByText("Could not open your setup")).toBeTruthy();
    });
});

describe("helpers", () => {
    it("starts from the browser's language when offered", () => {
        expect(preferredLanguage(languages, "ta-IN")).toBe("ta");
        expect(preferredLanguage(languages, "fr-FR")).toBe("en");
        expect(preferredLanguage([...languages, { code: "od", native: "ଓଡ଼ିଆ", english: "Odia", voice: true }], "or-IN")).toBe("od");
    });

    it("opens Chat with the task, or plain Chat without one", () => {
        expect(chatWithTask("  ")).toBe("/overview");
        expect(chatWithTask("Plan & go")).toBe("/overview?ask=Plan%20%26%20go");
    });
});
