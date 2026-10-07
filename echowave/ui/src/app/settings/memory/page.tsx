"use client";

import { Suspense } from "react";

import { MemorySettings } from "@/components/settings/pages/MemorySettings";

export default function MemoryPage() {
  return (
    <Suspense fallback={null}>
      <MemorySettings />
    </Suspense>
  );
}
