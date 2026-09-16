/** The little markdown a model writes, rendered rather than printed.
 *
 *  A bot's reply arrives as `**Wednesday Web Drop** — Mobbin`, because that
 *  is what a chat model writes when nothing tells it otherwise. Nothing here
 *  rendered it, so the asterisks went on screen exactly as typed. There is no
 *  markdown library in this project and this does not add one.
 *
 *  **Deliberately tiny.** Bold, italic and inline code, and nothing else. Not
 *  a subset of markdown chosen for elegance -- a subset chosen because of
 *  what these messages carry. A reply relaying a person's inbox contains
 *  subject lines written by strangers, one of which the bot itself flagged as
 *  probable phishing. So there is no raw HTML, no link parsing and no image
 *  syntax: every branch here returns text that React escapes, and the only
 *  thing that changes is which tag it sits in.
 *
 *  Lists are left alone on purpose. `1.` and `-` already read correctly under
 *  `whitespace-pre-wrap`, so a block parser would buy nothing and would have
 *  to decide what a line break means. The asterisks were the only noise.
 */

export type Emphasis = 'bold' | 'italic' | 'code' | null;

export type EmphasisToken = { text: string; emphasis: Emphasis };

/** `**bold**`, then `*italic*`, then `` `code` ``.
 *
 *  Bold before italic because `**x**` also matches the italic pattern, and
 *  reading it as italic leaves a stray asterisk at each end -- which is worse
 *  than not rendering it at all.
 */
const PATTERN = /\*\*([^*\n]+)\*\*|\*([^*\n]+)\*|`([^`\n]+)`/g;

/** One line of a reply, split into what to emphasise and what to leave.
 *
 *  A marker with nothing between it (`****`), or one that opens and never
 *  closes, is not emphasis and is returned as the text it is: somebody
 *  writing about asterisks should see asterisks.
 */
export function emphasisTokens(text: string): EmphasisToken[] {
    const out: EmphasisToken[] = [];
    let last = 0;
    for (const match of text.matchAll(PATTERN)) {
        const start = match.index ?? 0;
        if (start > last) out.push({ text: text.slice(last, start), emphasis: null });
        if (match[1] !== undefined) out.push({ text: match[1], emphasis: 'bold' });
        else if (match[2] !== undefined) out.push({ text: match[2], emphasis: 'italic' });
        else out.push({ text: match[3], emphasis: 'code' });
        last = start + match[0].length;
    }
    if (last < text.length) out.push({ text: text.slice(last), emphasis: null });
    return out;
}
