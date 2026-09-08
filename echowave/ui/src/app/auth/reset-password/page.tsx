import { PasswordRecoveryForm } from "@/components/auth/PasswordRecoveryForm";

export const metadata = { title: "Password recovery | Decibyl", robots: { index: false, follow: false }, referrer: "no-referrer" as const };

export default function Page() { return <PasswordRecoveryForm reset />; }
