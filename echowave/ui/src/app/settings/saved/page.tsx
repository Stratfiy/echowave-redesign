"use client";

import { Suspense } from "react";

import { SavedSettings } from "@/components/settings/pages/SavedSettings";

export default function SavedPage() {
  return (
    <Suspense fallback={null}>
      <SavedSettings />
    </Suspense>
  );
}
