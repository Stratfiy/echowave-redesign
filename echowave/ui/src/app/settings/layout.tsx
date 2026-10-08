"use client";

import { usePathname } from "next/navigation";

import { PushManifest } from "@/components/identity/PushManifest";
import { isSettingsRoot, SettingsNav } from "@/components/settings/SettingsNav";
import { cn } from "@/lib/utils";

/** Every section of Settings, with the grouped list of them beside it (or,
 *  on a phone at /settings, instead of it). */
export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const root = isSettingsRoot(usePathname() ?? "");
  return (
    <div className="flex min-h-full flex-col md:flex-row">
      <PushManifest />
      <SettingsNav />
      <div className={cn("min-w-0 flex-1", root && "hidden md:block")}>{children}</div>
    </div>
  );
}
