/**
 * Sign in and sign up against local auth (/api/v1/auth/login, /auth/signup),
 * through the generated client.
 *
 * Two answers need more than "it failed":
 *
 * - MFA: login returns 401 `mfa_required` (header `X-MFA-Required: totp`)
 *   when the account has an authenticator and no code was sent. That is a
 *   next step, not an error: the screen asks for the code and re-posts.
 * - Email verification: signup may say `email_verification_required`; the
 *   person types the emailed code before entering the workspace.
 */
import { loginApiV1AuthLoginPost, signupApiV1AuthSignupPost, verifyEmailApiV1AuthEmailVerifyPost } from '@/client/sdk.gen';
import type { AuthResponse } from '@/client/types.gen';
import { ApiError, call } from '@/lib/api';

/** The documents signup records acceptance of (api/services/compliance/agreements.py). */
export const SIGNUP_AGREEMENTS = ['terms', 'privacy'] as const;

export const LEGAL_LINKS = {
    terms: 'https://decibyl.ai/legal/terms',
    privacy: 'https://decibyl.ai/legal/privacy',
} as const;

export type SignInResult =
    | { kind: 'signed_in'; auth: AuthResponse }
    | { kind: 'mfa_required' }
    | { kind: 'error'; message: string; status: number };

export type SignInInput = { email: string; password: string; mfaCode?: string };

export function isMfaRequired(error: unknown): boolean {
    if (!(error instanceof ApiError) || error.status !== 401) return false;
    return error.detail === 'mfa_required' || Boolean(error.headers['x-mfa-required']);
}

export async function signIn(input: SignInInput): Promise<SignInResult> {
    try {
        const auth = await call(
            loginApiV1AuthLoginPost({
                body: {
                    email: input.email.trim(),
                    password: input.password,
                    mfa_code: input.mfaCode?.trim() || null,
                },
            }),
        );
        return { kind: 'signed_in', auth };
    } catch (error) {
        if (isMfaRequired(error)) return { kind: 'mfa_required' };
        const status = error instanceof ApiError ? error.status : 0;
        const message =
            status === 401
                ? input.mfaCode
                    ? 'That code did not work. Try the next one from your authenticator app.'
                    : 'That email and password do not match an account.'
                : error instanceof Error
                  ? error.message
                  : 'Could not sign in. Check your connection and try again.';
        return { kind: 'error', message, status };
    }
}

export type SignUpInput = {
    email: string;
    password: string;
    name?: string;
    inviteCode?: string;
    agreed: boolean;
};

export type SignUpResult =
    | { kind: 'signed_up'; auth: AuthResponse }
    | { kind: 'error'; message: string; status: number };

/** Problems found before anything is sent, in the order the form shows them. */
export function signUpProblems(input: SignUpInput): string[] {
    const problems: string[] = [];
    if (!/^\S+@\S+\.\S+$/.test(input.email.trim())) problems.push('Enter a valid email address.');
    // The server counts bytes (bcrypt's 72-byte limit), not characters.
    const bytes = new TextEncoder().encode(input.password).length;
    if (input.password.length < 8) problems.push('Choose a password of at least 8 characters.');
    else if (bytes > 72) problems.push('That password is too long. Use a shorter one.');
    if (!input.agreed) problems.push('Tick the box to agree before creating your account.');
    return problems;
}

export async function signUp(input: SignUpInput): Promise<SignUpResult> {
    const problems = signUpProblems(input);
    if (problems.length) return { kind: 'error', message: problems[0], status: 0 };
    try {
        const auth = await call(
            signupApiV1AuthSignupPost({
                body: {
                    email: input.email.trim(),
                    password: input.password,
                    name: input.name?.trim() || null,
                    invite_code: input.inviteCode?.trim() || null,
                    accepted_agreements: [...SIGNUP_AGREEMENTS],
                },
            }),
        );
        return { kind: 'signed_up', auth };
    } catch (error) {
        return {
            kind: 'error',
            message: error instanceof Error ? error.message : 'Could not create the account.',
            status: error instanceof ApiError ? error.status : 0,
        };
    }
}

export async function verifyEmail(code: string): Promise<void> {
    await call(verifyEmailApiV1AuthEmailVerifyPost({ body: { code: code.trim() } }));
}
