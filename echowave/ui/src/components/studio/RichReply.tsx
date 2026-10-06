/**
 * A reply, lightly formatted: paragraphs, bullet and numbered lists, and
 * **bold** and `code` inside a line.
 *
 * Built as React nodes, never as HTML, so whatever the model writes is text
 * and cannot become markup on this page. Anything that is not one of these
 * few shapes is shown exactly as written.
 */

import { Fragment, type ReactNode } from "react";

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`)/g;

function inline(text: string): ReactNode[] {
    return text.split(INLINE).map((part, index) => {
        if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
            return (
                <strong key={index} className="font-semibold text-foreground">
                    {part.slice(2, -2)}
                </strong>
            );
        }
        if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
            return (
                <code key={index} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">
                    {part.slice(1, -1)}
                </code>
            );
        }
        return <Fragment key={index}>{part}</Fragment>;
    });
}

type Block =
    | { kind: "p"; lines: string[] }
    | { kind: "ul"; items: string[] }
    | { kind: "ol"; items: string[] };

const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;

/** The reply split into paragraphs and lists. Exported for tests. */
export function blocksOf(text: string): Block[] {
    const blocks: Block[] = [];
    for (const line of text.replace(/\r\n/g, "\n").split("\n")) {
        const bullet = line.match(BULLET);
        const numbered = bullet ? null : line.match(NUMBERED);
        const last = blocks[blocks.length - 1];
        if (bullet) {
            if (last?.kind === "ul") last.items.push(bullet[1]);
            else blocks.push({ kind: "ul", items: [bullet[1]] });
        } else if (numbered) {
            if (last?.kind === "ol") last.items.push(numbered[1]);
            else blocks.push({ kind: "ol", items: [numbered[1]] });
        } else if (!line.trim()) {
            blocks.push({ kind: "p", lines: [] });
        } else if (last?.kind === "p") {
            last.lines.push(line);
        } else {
            blocks.push({ kind: "p", lines: [line] });
        }
    }
    return blocks.filter((b) => (b.kind === "p" ? b.lines.length > 0 : true));
}

export function RichReply({ text }: { text: string }) {
    return (
        <div className="space-y-3 break-words text-[15px] leading-relaxed text-foreground/90">
            {blocksOf(text).map((block, index) => {
                if (block.kind === "ul") {
                    return (
                        <ul key={index} className="list-disc space-y-1 pl-5 marker:text-muted-foreground">
                            {block.items.map((item, i) => (
                                <li key={i}>{inline(item)}</li>
                            ))}
                        </ul>
                    );
                }
                if (block.kind === "ol") {
                    return (
                        <ol key={index} className="list-decimal space-y-1 pl-5 marker:text-muted-foreground">
                            {block.items.map((item, i) => (
                                <li key={i}>{inline(item)}</li>
                            ))}
                        </ol>
                    );
                }
                return (
                    <p key={index} className="whitespace-pre-wrap">
                        {block.lines.map((line, i) => (
                            <Fragment key={i}>
                                {i > 0 ? "\n" : null}
                                {inline(line)}
                            </Fragment>
                        ))}
                    </p>
                );
            })}
        </div>
    );
}
