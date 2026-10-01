import { describe, expect, it } from "vitest";

import { signupSteps, stepForSignupError, validateSignupStep } from "../signupSteps";

const valid = {
  email: "asha@example.com",
  inviteCode: "ABCD-2345",
  name: "Asha",
  password: "correct-horse",
  agreed: true,
};

describe("the sign-up steps", () => {
  it("asks the invite only while it is required", () => {
    expect(signupSteps({ inviteRequired: false, path: "email" })).toEqual(["start", "name", "password", "agreement"]);
    expect(signupSteps({ inviteRequired: true, path: "email" })).toEqual([
      "start",
      "invite",
      "name",
      "password",
      "agreement",
    ]);
  });

  it("stops Google after the invite, since Google supplies the rest", () => {
    expect(signupSteps({ inviteRequired: true, path: "google" })).toEqual(["start", "invite"]);
    expect(signupSteps({ inviteRequired: false, path: "google" })).toEqual(["start"]);
  });

  it("mirrors the server's password limits, in bytes", () => {
    expect(validateSignupStep("password", { ...valid, password: "short" })).toMatch(/at least 8/);
    // 30 Devanagari characters are 90 bytes: over bcrypt's 72.
    expect(validateSignupStep("password", { ...valid, password: "क".repeat(30) })).toMatch(/too long/);
    expect(validateSignupStep("password", valid)).toBeNull();
  });

  it("will not pass the agreement unticked", () => {
    expect(validateSignupStep("agreement", { ...valid, agreed: false })).toMatch(/agree/);
    expect(validateSignupStep("agreement", valid)).toBeNull();
  });

  it("sends each refusal back to the step it is about", () => {
    expect(stepForSignupError(409, "Email already registered")).toBe("start");
    expect(stepForSignupError(403, "That invite code has expired.")).toBe("invite");
    expect(stepForSignupError(403, "Signup is disabled")).toBe("agreement");
    expect(stepForSignupError(400, "Please accept the Terms to create an account.")).toBe("agreement");
    expect(stepForSignupError(422, "password: Password must be at least 8 characters")).toBe("password");
    expect(stepForSignupError(422, "email: value is not a valid email address")).toBe("start");
  });
});
