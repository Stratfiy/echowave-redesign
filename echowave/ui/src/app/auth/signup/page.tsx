"use client";

import { Suspense } from "react";

import { SignupFlow } from "./SignupFlow";

/**
 * `useSearchParams` needs a Suspense boundary, or this route opts out of static
 * rendering at build time.
 */
export default function SignupPage() {
  return (
    <Suspense fallback={null}>
      <SignupFlow />
    </Suspense>
  );
}
