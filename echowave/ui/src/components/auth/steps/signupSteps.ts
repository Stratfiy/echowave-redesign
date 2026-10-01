/**
 * The sign-up flow as data: which questions are asked, in what order, and
 * which one a server refusal belongs to. Kept free of React so the rules can
 * be tested on their own and the page stays a thin renderer.
 */
import { AUTH_COPY } from "./copy";

export type SignupStep = "start" | "invite" | "name" | "password" | "agreement";

/** Which door the person chose on the first screen. */
export type SignupPath = "email" | "google";

/** The steps for this person, in order. The invite step is only asked when
 *  the deployment requires one; Google stops after it, because Google's own
 *  page supplies the name and there is no password. */
export function signupSteps({
  inviteRequired,
  path,
}: {
  inviteRequired: boolean;
  path: SignupPath;
}): SignupStep[] {
  const invite: SignupStep[] = inviteRequired ? ["invite"] : [];
  if (path === "google") return ["start", ...invite];
  return ["start", ...invite, "name", "password", "agreement"];
}

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MIN_PASSWORD = 8;
const MAX_PASSWORD_BYTES = 72;

export interface SignupValues {
  email: string;
  inviteCode: string;
  name: string;
  password: string;
  agreed: boolean;
}

/** The problem with this step's answer, or null when it may advance. Mirrors
 *  the server's own checks so a refusal is caught before the round trip. */
export function validateSignupStep(step: SignupStep, values: SignupValues): string | null {
  const copy = AUTH_COPY.signup;
  switch (step) {
    case "start":
      return EMAIL_PATTERN.test(values.email.trim()) ? null : copy.start.emailInvalid;
    case "invite":
      return values.inviteCode.trim() ? null : copy.invite.required;
    case "name":
      return values.name.trim() ? null : copy.name.required;
    case "password":
      if (values.password.length < MIN_PASSWORD) return copy.password.tooShort;
      if (new TextEncoder().encode(values.password).length > MAX_PASSWORD_BYTES) {
        return copy.password.tooLong;
      }
      return null;
    case "agreement":
      return values.agreed ? null : copy.agreement.required;
  }
}

/** The step a refused sign-up sends the person back to.
 *
 *  409 is a taken email; 403 is a refused invite (or sign-up switched off,
 *  which stays where it is); 400 is the click-wrap; a 422 names its field in
 *  FastAPI's `loc`, and only the password and email have server-side rules. */
export function stepForSignupError(status: number | undefined, detail: string): SignupStep {
  const text = detail.toLowerCase();
  if (status === 409 || text.includes("already registered")) return "start";
  if (status === 403 && !text.includes("signup is disabled")) return "invite";
  if (text.includes("invite")) return "invite";
  if (status === 400 && text.includes("accept")) return "agreement";
  if (text.includes("password")) return "password";
  if (text.includes("email")) return "start";
  return "agreement";
}
