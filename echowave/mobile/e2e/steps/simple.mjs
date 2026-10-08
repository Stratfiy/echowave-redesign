export default async () => {
  await go('/settings');
  await tid('settings-simple').waitFor();
  await page.getByRole('switch').first().click();
  await page.waitForTimeout(2000);
  await go('/');
  await tid('simple-talk').waitFor();
  await shot('28-simple-mode-chat', 1500);
  await go('/settings');
  await page.getByRole('switch').first().click();
  await page.waitForTimeout(2000);
};
