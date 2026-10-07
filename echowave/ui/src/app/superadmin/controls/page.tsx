"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useStaffConsole } from "@/components/staff/StaffShell";

const ORDER: Array<[string, string]> = [
    ["providers.read", "/superadmin/controls/providers"],
    ["policy.read", "/superadmin/controls/policy"],
    ["roles.manage", "/superadmin/controls/roles"],
    ["audit.read", "/superadmin/controls/audit"],
];

/** Controls and audit opens on the first part this person's role includes. */
export default function ControlsIndex() {
    const { can } = useStaffConsole();
    const router = useRouter();
    const target = ORDER.find(([cap]) => can(cap))?.[1];
    useEffect(() => {
        if (target) router.replace(target);
    }, [router, target]);
    return target ? null : <p className="text-sm text-muted-foreground">Your role includes no part of Controls and audit.</p>;
}
