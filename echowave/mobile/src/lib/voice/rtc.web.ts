/** WebRTC in the browser, for the web build of the app. */
const g = globalThis as unknown as {
    RTCPeerConnection: typeof RTCPeerConnection;
    navigator: Navigator;
    Audio: typeof Audio;
};

export const RTCPeerConnectionImpl = g.RTCPeerConnection;
export { RTCPeerConnectionImpl as RTCPeerConnection };
export const mediaDevices = g.navigator?.mediaDevices;

let audio: HTMLAudioElement | null = null;

export function playRemote(stream: MediaStream): void {
    audio ??= new g.Audio();
    audio.autoplay = true;
    audio.srcObject = stream;
    void audio.play().catch(() => undefined);
}

export function stopRemote(): void {
    if (audio) audio.srcObject = null;
}
