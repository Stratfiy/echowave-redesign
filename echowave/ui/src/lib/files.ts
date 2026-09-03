import { getSignedUrlApiV1S3SignedUrlGet } from "@/client/sdk.gen";
import { detailFromResult } from "@/lib/apiError";

/**
 * Why a signed URL could not be issued, in the caller's terms.
 *
 * Kept alongside the URL rather than logged and dropped. A caller that gets
 * only `null` cannot tell "this run has no recording" from "the recording
 * exists and we were refused it", and those need different words on screen and
 * different actions from whoever reads them. The media dialog said the first
 * for years while meaning either.
 */
export interface SignedUrlResult {
    url: string | null;
    error: string | null;
}

/**
 * Ask for a signed URL, reporting why not.
 *
 * The generated client resolves rather than throws on a 4xx/5xx, so a
 * `try/catch` catches only network failures and every refusal from the server
 * slips through as a falsy `data` — see `ui/AGENTS.md`. This checks
 * `response.error`, which is the difference between "no recording" and
 * "403: this run belongs to another organization".
 */
async function requestSignedUrl(
    key: string,
    inline: boolean,
): Promise<SignedUrlResult> {
    try {
        const response = await getSignedUrlApiV1S3SignedUrlGet({
            query: { key, inline },
        });

        if (response.error) {
            return {
                url: null,
                error: detailFromResult(response, 'Could not open this file'),
            };
        }
        if (response.data?.url) {
            return { url: response.data.url as string, error: null };
        }
        // A 200 with nothing in it. Rare, and worth its own sentence: the
        // server said yes and gave us nothing, which is not the same as a
        // refusal and should not be reported as one.
        return { url: null, error: 'The server returned no URL for this file.' };
    } catch (error) {
        console.error('Error getting signed URL:', error);
        return {
            url: null,
            error: 'Could not reach the server. Check your connection and try again.',
        };
    }
}

/**
 * Get a signed URL and download a file.
 *
 * Returns the failure rather than swallowing it, so a caller can say something
 * when the download does not happen. A button that silently does nothing is
 * indistinguishable from a button that is broken.
 */
export async function downloadFile(url: string | null): Promise<string | null> {
    if (!url) return null;

    const { url: signed, error } = await requestSignedUrl(url, false);
    if (signed) {
        window.open(signed, '_blank');
        return null;
    }
    return error;
}

/**
 * Return a signed URL for a given S3 key without triggering a download.
 * Useful for previewing media (audio or transcript) in-browser first.
 */
export async function getSignedUrl(
    url: string | null,
    inline: boolean = false,
): Promise<SignedUrlResult> {
    if (!url) return { url: null, error: null };
    return requestSignedUrl(url, inline);
}
