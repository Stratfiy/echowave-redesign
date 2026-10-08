/**
 * WebRTC on a phone: react-native-webrtc (a native module, so the app needs a
 * development or store build, not Expo Go). Remote audio plays through the
 * phone's audio session on its own; there is no <audio> element to drive.
 * The web build uses rtc.web.ts instead.
 */
import { mediaDevices, RTCPeerConnection } from 'react-native-webrtc';

export { mediaDevices, RTCPeerConnection };

export function playRemote(_stream: unknown): void {
    // Played by the native audio session.
}

export function stopRemote(): void {
    // Nothing held.
}
