/**
 * The door's words in one place. The value line is the founder's
 * positioning (9 Oct 2026), the same as the sign-in headline
 * (components/auth/AuthShell.tsx), reused rather than rewritten.
 */
export const EARLY_ACCESS_COPY = {
    title: "Early access to Decibyl",
    lead: "An intelligent agent that grows and evolves with you. Decibyl is invite-only for now; leave your email and we will review it.",
    email: "Email",
    emailHint: "We send your invitation here.",
    name: "Your name (optional)",
    language: "Language",
    firstTask: "What would you ask Decibyl first? (optional)",
    firstTaskPlaceholder: "For example: remind my customers about payments due this week",
    phone: "Phone (optional)",
    occupation: "What you do (optional)",
    submit: "Join the waitlist",
    submitting: "Sending…",
    invalidEmail: "Enter an email address like name@example.com.",
    failed: "Your request was not sent. Check your connection and try again.",
    waitlisted: {
        title: "You are on the list",
        body: "We review every request and send invitations by email. There is nothing more to do now.",
    },
    alreadyOnList: {
        title: "You are already on the list",
        body: "We have this address from before, so nothing new was added. Invitations go out by email.",
    },
    alreadyRegistered: {
        title: "This address already has an account",
        body: "Sign in to continue where you left off.",
    },
    invited: {
        title: "Your invitation is waiting",
        body: "We already sent an invitation to this address. Open the link in that email to accept it.",
    },
    signIn: "Sign in",
} as const;

export const INVITATION_COPY = {
    loading: "Checking your invitation…",
    valid: {
        title: "You are invited to Decibyl",
        body: "Accept to create your account. We check this invitation again before anything is set up.",
        accept: "Accept invitation",
        boundTo: (hint: string) => `For ${hint}. Sign up with this address.`,
        until: (when: string) => `Valid until ${when}.`,
    },
    expired: { title: "This invitation has expired", body: "Invitations last a limited time. Ask for a new one below." },
    revoked: { title: "This invitation was withdrawn", body: "It can no longer be used. Ask for a new one below." },
    used: { title: "This invitation has been used", body: "If it was you, sign in. Otherwise ask for a new one below." },
    invalid: { title: "We could not find this invitation", body: "Check the link in your email, or ask for a new invitation." },
    requestNew: "Request a new invitation",
    failed: "Could not check this invitation",
    notOpen: "Early access is not open yet.",
} as const;
