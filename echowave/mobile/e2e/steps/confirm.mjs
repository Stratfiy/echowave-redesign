export default async () => {
  await go('/chat/7d3c2a10-5b1e-4c8f-9a6d-2f0e8b4c1a99');
  await tid('card-3-confirm').waitFor();
  // A double tap: two presses before the first answer comes back.
  const button = tid('card-3-confirm');
  await Promise.all([button.dispatchEvent('click'), button.dispatchEvent('click')]);
  await shot('06-card-confirmed-undo-window', 1500);
  await page.waitForTimeout(13000);
  await go('/chat/7d3c2a10-5b1e-4c8f-9a6d-2f0e8b4c1a99');
  await shot('07-card-done', 2500);
};
