export default async () => {
  await go('/settings/language');
  await tid('locale-hi').click();
  await page.waitForTimeout(800);
  await shot('29-hindi-language', 800);
  await go('/');
  await shot('30-hindi-chat', 1500);
  await go('/today');
  await shot('31-hindi-today', 2000);
  await go('/chat/7d3c2a10-5b1e-4c8f-9a6d-2f0e8b4c1a99');
  await shot('32-hindi-thread', 2500);
  await go('/settings/language');
  await tid('locale-en').click();
  await page.waitForTimeout(800);
};
