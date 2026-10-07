export default async () => {
  await go('/voice');
  await tid('screen-voice').waitFor();
  await shot('18-voice-ready', 2500);
  if (await tid('voice-start').isEnabled()) {
    await tid('voice-start').click();
    await page.waitForTimeout(6000);
    await shot('19-voice-session', 200);
  }
};
