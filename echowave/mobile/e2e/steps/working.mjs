export default async () => {
  await go('/');
  await tid('new-chat').click();
  await tid('composer-input').waitFor();
  await shot('08-new-chat-empty', 1500);
  await tid('composer-input').fill('What is due this week?');
  await tid('composer-send').click();
  await page.waitForTimeout(2500);
  await shot('09-thread-working', 200);
};
