import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const submitOnboarding = vi.hoisted(() => vi.fn(async () => {}));
vi.mock("../submitOnboarding", () => ({ submitOnboarding }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { email: "a@b.in", provider: "local" } }) }));
vi.mock("@/context/AppConfigContext", () => ({ useAppConfig: () => ({ config: { deploymentMode: "oss" } }) }));

import { OnboardingModal } from "../OnboardingModal";

describe("OnboardingModal", () => {
  it("asks three things at the door: role, business, where heard, and nothing about calls", () => {
    render(<OnboardingModal open onComplete={() => {}} />);
    expect(screen.getByText("What's your role?")).toBeTruthy();
    expect(screen.getByText("What business are you in?")).toBeTruthy();
    expect(screen.getByText("Where did you hear about us?")).toBeTruthy();
    expect(screen.queryByText(/call volume/i)).toBeNull();
    expect(screen.queryByText(/migrating/i)).toBeNull();
    // Compulsory: the one exit is disabled until all three are answered.
    expect(screen.getByRole("button", { name: "Get started" }).hasAttribute("disabled")).toBe(true);
  });
});
