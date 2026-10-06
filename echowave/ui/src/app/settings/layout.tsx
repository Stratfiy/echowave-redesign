import { SettingsNav } from "@/components/settings/SettingsNav";

/** Every section of Settings, with the list of them beside it. */
export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-full flex-col md:flex-row">
      <SettingsNav />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
