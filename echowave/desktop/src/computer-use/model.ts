/**
 * The one call that leaves the machine: the model, with the screenshots.
 *
 * Direct from this computer to the Claude API (or to a relay the operator
 * names with `baseURL` that forwards and keeps nothing). Never through the
 * Decibyl backend: the backend only ever sees the text of a held step.
 */

import Anthropic from '@anthropic-ai/sdk';

import type { ModelClient, ModelRequest, ModelResponse } from './types';
import type { Rates } from './limits';

export interface ModelSettings {
    apiKey: string;
    baseURL?: string;
    model: string;
    effort: 'low' | 'medium' | 'high' | 'xhigh' | 'max';
    rates: Rates;
}

/** The app's default. Per-token rates are USD per million (see README). */
export const DEFAULT_MODEL: Omit<ModelSettings, 'apiKey'> = {
    model: 'claude-opus-5-5',
    effort: 'high',
    rates: { input: 4, output: 20, cacheRead: 0.2, cacheWrite: 5 },
};

export function anthropicModel(settings: ModelSettings): ModelClient {
    const client = new Anthropic({
        apiKey: settings.apiKey,
        baseURL: settings.baseURL || undefined,
    });
    return {
        async create(request: ModelRequest, signal: AbortSignal): Promise<ModelResponse> {
            const params = {
                model: settings.model,
                max_tokens: 16000,
                thinking: { type: 'adaptive' },
                output_config: { effort: settings.effort },
                // Caches the tools and system prompt, and the history up to
                // the latest turn, so each step pays for one new screenshot.
                cache_control: { type: 'ephemeral' },
                system: request.system,
                tools: request.tools,
                messages: request.messages,
                // A safety refusal is retried on a fallback model chosen by
                // the server; a refusal that stands ends the task honestly.
                betas: ['server-side-fallback-2026-07-01'],
                fallbacks: 'default',
            };
            // The SDK's types lag the request surface; the body is sent as is.
            const response = await client.beta.messages.create(params as never, { signal });
            return response as unknown as ModelResponse;
        },
    };
}
