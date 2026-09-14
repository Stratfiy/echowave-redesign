'use client';

/**
 * Talk instead of type: the microphone, a level meter, and the transcript.
 *
 * One hook for every composer -- a bot's chat, a channel, Decibyl -- so
 * voice input behaves the same everywhere. It records with the browser's
 * MediaRecorder, reads the input level from an AnalyserNode so the screen
 * can draw a waveform while listening, and on stop sends the clip to the
 * account's own speech-to-text (the same provider its calls use; managed
 * accounts get the Indic default). The words come back to the caller, who
 * puts them in the box. Nothing is sent until the person stops: a recording
 * that streams as it goes would be a recording they cannot cancel.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { transcribeAudioApiV1WorkflowRecordingsTranscribePost } from '@/client/sdk.gen';
import { detailFromResult } from '@/lib/apiError';

/** How many recent levels the waveform shows. */
export const BARS = 24;
/** Longest clip we will send. A message, not a memo. */
export const MAX_SECONDS = 60;

/** The transcript appended to what is already typed, with one space between. */
export function appendDictation(text: string, transcript: string): string {
    const words = transcript.trim();
    if (!words) return text;
    if (!text.trim()) return words;
    return `${text.replace(/\s+$/, '')} ${words}`;
}

export function canDictate(): boolean {
    return (
        typeof window !== 'undefined' &&
        typeof navigator !== 'undefined' &&
        !!navigator.mediaDevices?.getUserMedia &&
        typeof window.MediaRecorder !== 'undefined'
    );
}

export function useDictation(onText: (transcript: string) => void) {
    const [listening, setListening] = useState(false);
    const [transcribing, setTranscribing] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [levels, setLevels] = useState<number[]>(() => Array(BARS).fill(0));
    const recorder = useRef<MediaRecorder | null>(null);
    const stream = useRef<MediaStream | null>(null);
    const audio = useRef<AudioContext | null>(null);
    const chunks = useRef<Blob[]>([]);
    const meter = useRef<number | null>(null);
    const limit = useRef<number | null>(null);
    const onTextRef = useRef(onText);
    onTextRef.current = onText;

    const teardown = useCallback(() => {
        if (meter.current !== null) window.clearInterval(meter.current);
        if (limit.current !== null) window.clearTimeout(limit.current);
        meter.current = null;
        limit.current = null;
        stream.current?.getTracks().forEach((track) => track.stop());
        stream.current = null;
        void audio.current?.close();
        audio.current = null;
        recorder.current = null;
        setLevels(Array(BARS).fill(0));
    }, []);

    useEffect(() => teardown, [teardown]);

    const stop = useCallback(() => {
        const current = recorder.current;
        if (!current || current.state === 'inactive') return;
        current.stop();
    }, []);

    const start = useCallback(async () => {
        if (listening || transcribing) return;
        setError(null);
        if (!canDictate()) {
            setError('This browser cannot record audio.');
            return;
        }
        let media: MediaStream;
        try {
            media = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch {
            setError('Microphone access was refused.');
            return;
        }
        stream.current = media;
        chunks.current = [];
        const current = new MediaRecorder(media);
        recorder.current = current;
        current.ondataavailable = (event) => {
            if (event.data.size > 0) chunks.current.push(event.data);
        };
        current.onstop = async () => {
            const type = current.mimeType || 'audio/webm';
            const blob = new Blob(chunks.current, { type });
            teardown();
            setListening(false);
            if (blob.size === 0) return;
            setTranscribing(true);
            const extension = type.includes('webm') ? 'webm' : type.includes('ogg') ? 'ogg' : 'mp4';
            const file = new File([blob], `dictation.${extension}`, { type });
            // "unknown" asks the provider to detect the language: a person
            // may speak Tamil into a box whose placeholder is English.
            const response = await transcribeAudioApiV1WorkflowRecordingsTranscribePost({
                body: { file, language: 'unknown' },
            });
            setTranscribing(false);
            if (response.error) {
                setError(detailFromResult(response, 'Could not hear that'));
                return;
            }
            const transcript = (response.data as { transcript?: string } | undefined)?.transcript ?? '';
            if (transcript.trim()) onTextRef.current(transcript);
            else setError('Nothing was heard.');
        };

        // The level meter, for the waveform. Cosmetic: a browser without
        // AudioContext still records and transcribes.
        try {
            const context = new AudioContext();
            audio.current = context;
            const analyser = context.createAnalyser();
            analyser.fftSize = 256;
            context.createMediaStreamSource(media).connect(analyser);
            const buffer = new Uint8Array(analyser.frequencyBinCount);
            meter.current = window.setInterval(() => {
                analyser.getByteTimeDomainData(buffer);
                let peak = 0;
                for (const sample of buffer) peak = Math.max(peak, Math.abs(sample - 128) / 128);
                setLevels((was) => [...was.slice(1), Math.min(1, peak * 1.6)]);
            }, 80);
        } catch {
            // No meter; still listening.
        }

        current.start();
        setListening(true);
        limit.current = window.setTimeout(stop, MAX_SECONDS * 1000);
    }, [listening, transcribing, stop, teardown]);

    return { listening, transcribing, error, levels, start, stop };
}
