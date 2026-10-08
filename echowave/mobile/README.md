# Decibyl for iOS and Android

The native app. What it does, which screens are native and which are web
views, the server side and the switches: [`../MOBILE.md`](../MOBILE.md).
Going to TestFlight and Play: [`RELEASE.md`](RELEASE.md).

## Run it

```bash
cd echowave/mobile
npm ci
npm run typecheck && npm run lint && npm test

# Against a local API (uvicorn on :8000) in the browser:
EXPO_PUBLIC_API_URL=http://localhost:8000 EXPO_PUBLIC_WEB_URL=http://localhost:3000 npx expo start --web

# On a phone: a development build (native modules: WebRTC, contacts,
# notifications, the share extension), then the dev server.
npx eas-cli@latest build --profile development -p android   # or -p ios
npx expo start --dev-client
```

Expo Go cannot run the app (react-native-webrtc and the share extension are
native code); use a development build.

## Regenerate the API client

```bash
# from echowave/, with the API's environment loaded
python -m scripts.dump_docs_openapi      # writes ui/openapi.internal.json
cd mobile && npm run generate-client     # writes src/client (committed)
```

## Layout

```
mobile/
├── index.ts               entry: background tasks, then Expo Router
├── app.config.ts          name, ids, permissions, plugins (values from eas.json env)
├── eas.json               build profiles (development / preview / production) and submit
├── openapi-ts.config.ts   the generated client's config
├── src/app/               screens (Expo Router: every file is a route)
├── src/components/        building blocks, chat rows, composer, web view
├── src/client/            generated from ui/openapi.internal.json -- do not edit
├── src/lib/               auth, API, chat, approvals, contacts, people, push,
│                          voice, i18n (en, hi), theme, links, web screens
├── src/lib/__tests__/     jest-expo tests
├── e2e/                   the phone-size walkthrough (Playwright on the web build)
├── universal-links/       .well-known templates for app.decibyl.ai
└── RELEASE.md             accounts -> TestFlight and Play internal testing
```
