/**
 * Every customer-facing string on the sign-up, log-in and verify screens, in
 * one place for copy review. Components read from here and never inline text.
 */
export const AUTH_COPY = {
  back: "Back",
  continue: "Continue",
  or: "or",
  progressLabel: "Progress",
  networkError: "Could not reach the server. Check your connection and try again.",

  signup: {
    start: {
      title: "Create your account",
      hint: "Start with Google, or your email.",
      google: "Continue with Google",
      googleNotice: "Continuing with Google means you agree to the",
      emailLabel: "Email",
      emailPlaceholder: "you@company.com",
      emailInvalid: "Enter a valid email address.",
      haveAccount: "Already have an account?",
      signIn: "Sign in",
    },
    invite: {
      title: "Your invite code",
      hint: "Decibyl is invite-only for now. The code is in your invite email.",
      label: "Invite code",
      placeholder: "ABCD-2345",
      required: "Enter your invite code to continue.",
      toGoogle: "Continue to Google",
    },
    name: {
      title: "What should we call you?",
      hint: "Your name, as teammates will see it.",
      label: "Full name",
      placeholder: "Your name",
      required: "Enter your name to continue.",
    },
    password: {
      title: "Choose a password",
      hint: "At least 8 characters.",
      label: "Password",
      show: "Show password",
      hide: "Hide password",
      tooShort: "Password must be at least 8 characters.",
      tooLong:
        "Password is too long. It must be at most 72 bytes — roughly 72 characters, or fewer if it uses non-Latin script.",
    },
    agreement: {
      title: "One last thing",
      hint: "Read them before you create the account.",
      agreePrefix: "I agree to the",
      terms: "Terms",
      and: "and",
      privacy: "Privacy Notice",
      required: "Tick the box to agree before creating your account.",
      submit: "Create account",
      submitting: "Creating account…",
    },
    failed: "Sign-up failed. Try again.",
  },

  login: {
    email: {
      title: "Welcome back",
      hint: "Sign in to your Decibyl workspace.",
      google: "Continue with Google",
      noAccount: "Don't have an account?",
      signUp: "Sign up",
    },
    password: {
      title: "Enter your password",
      forgot: "Forgot password?",
      submit: "Sign in",
      submitting: "Signing in…",
      required: "Enter your password.",
    },
    mfa: {
      title: "Authentication code",
      hint: "From your authenticator app. If you have lost it, use one of the recovery codes you saved when you turned this on.",
      label: "Authentication code",
      placeholder: "6-digit code, or a recovery code",
      submit: "Verify",
      required: "Enter the code from your authenticator app.",
    },
    failed: "Sign-in failed. Try again.",
  },

  verify: {
    title: "Check your email",
    sentTo: "We sent a six-digit code to",
    yourAddress: "your address",
    bonus: "Enter it and your first 150 free credits land.",
    label: "Six-digit verification code",
    submit: "Verify and continue",
    resend: "Send the code again",
    later: "Do this later",
    rejected: "That code was not accepted.",
    resendFailed: "Could not send a code.",
    resent: "A new code is on its way.",
    recentlySent: "A code was sent recently — check your inbox, including spam.",
    verified: "Email verified.",
  },
} as const;

/**
 * In-app legal routes. `/trust` is public and links the full terms;
 * `/privacy` is the in-app data-protection page. Both open in a new tab so a
 * half-finished sign-up is not lost.
 */
export const LEGAL_LINKS = {
  terms: "/trust",
  privacy: "/privacy",
} as const;
