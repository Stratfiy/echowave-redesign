import Link from "next/link";

import type { DeployAgent } from "@/components/deploy/useDeployAgents";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";

/** The "which agent" row, identical on every DEPLOY screen. */
export function DeployAgentPicker({
    agents,
    selected,
    onSelect,
    label,
}: {
    agents: DeployAgent[];
    selected: DeployAgent | null;
    onSelect: (id: number) => void;
    label: string;
}) {
    return (
        <Card>
            <CardContent className="flex flex-wrap items-end gap-4 pt-6">
                <div className="min-w-0 flex-1 space-y-2">
                    <label htmlFor="deploy-agent" className="text-sm font-medium">
                        {label}
                    </label>
                    <Select
                        value={selected ? String(selected.id) : undefined}
                        onValueChange={(value) => onSelect(Number(value))}
                    >
                        <SelectTrigger id="deploy-agent" className="max-w-md">
                            <SelectValue placeholder="Choose an agent" />
                        </SelectTrigger>
                        <SelectContent>
                            {agents.map((agent) => (
                                <SelectItem key={agent.id} value={String(agent.id)}>
                                    {agent.name}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>
                {selected && (
                    <Button variant="outline" asChild>
                        <Link href={`/workflow/${selected.id}`}>Edit this agent</Link>
                    </Button>
                )}
            </CardContent>
        </Card>
    );
}
