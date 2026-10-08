/**
 * The one way screens call the API: the generated SDK functions, unwrapped.
 *
 * `call(sdkFn(...))` returns the data or throws an `ApiError` carrying the
 * status and FastAPI's `detail`, so a 409 or a 404 can never be mistaken for
 * success (the generated client does not throw on its own).
 */
import { client } from '@/client/client.gen';

import { ApiError, detailFromError } from './errors';

export { client };
export { ApiError, detailFromError };

type Result<T> = { data?: T; error?: unknown; response?: Response };

function headersOf(response: Response | undefined): Record<string, string> {
    const out: Record<string, string> = {};
    response?.headers?.forEach?.((value, key) => {
        out[key.toLowerCase()] = value;
    });
    return out;
}

export async function call<T>(pending: Promise<Result<T>>): Promise<T> {
    const result = await pending;
    if (result.error !== undefined && result.error !== null) {
        const status = result.response?.status ?? 0;
        throw new ApiError(status, (result.error as { detail?: unknown })?.detail ?? result.error, detailFromError(result.error), headersOf(result.response));
    }
    const status = result.response?.status ?? 200;
    if (status >= 400) {
        throw new ApiError(status, null, `Request failed (${status})`, headersOf(result.response));
    }
    return result.data as T;
}
