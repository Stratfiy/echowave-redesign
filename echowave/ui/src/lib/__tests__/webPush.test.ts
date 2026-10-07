import { describe, expect, it } from "vitest";

import { deviceLabel, pushPermission } from "../webPush";

describe("web push helpers", () => {
    it.each([
        ["Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/128.0 Mobile Safari/537.36", "Chrome on Android"],
        ["Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1", "Safari on iPhone or iPad"],
        ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36 Edg/128.0", "Edge on Windows"],
    ])("names the device a person will recognise (%#)", (ua, label) => {
        expect(deviceLabel(ua)).toBe(label);
    });

    it("says unsupported where the browser has no push", () => {
        expect(pushPermission()).toBe("unsupported");
    });
});
