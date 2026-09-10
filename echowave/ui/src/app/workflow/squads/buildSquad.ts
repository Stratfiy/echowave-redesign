/**
 * The squad the form describes, as a workflow definition.
 *
 * Kept apart from the page so the page exports only what Next.js allows, and
 * so the shape of what a squad is can be tested without rendering anything.
 */
/** The squad as a workflow definition the canvas and the runtime both read. */
export function buildSquadDefinition(input: {
    greeting: string;
    prompt: string;
    persona: string;
    members: { agentUuid: string; name: string; when: string }[];
}) {
    const nodes: Record<string, unknown>[] = [
        {
            id: "global-1",
            type: "globalNode",
            position: { x: -320, y: 0 },
            data: { name: "Rules", prompt: input.persona },
        },
        {
            id: "start-1",
            type: "startCall",
            position: { x: 0, y: 0 },
            data: {
                name: "Front desk",
                is_start: true,
                greeting: input.greeting,
                greeting_type: "text",
                prompt: input.prompt,
                allow_interrupt: true,
                add_global_prompt: true,
            },
        },
    ];
    const edges: Record<string, unknown>[] = [];
    input.members.forEach((member, index) => {
        const id = `handoff-${index + 1}`;
        nodes.push({
            id,
            type: "handoff",
            position: { x: 360, y: index * 160 },
            data: { name: member.name, agent_uuid: member.agentUuid },
        });
        edges.push({
            id: `start-1-${id}`,
            source: "start-1",
            target: id,
            data: { label: member.name, condition: member.when },
        });
    });
    return { nodes, edges, viewport: { x: 0, y: 0, zoom: 0.8 } };
}
