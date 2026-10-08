export default async () => {
  await go('/');
  await tid('new-chat').click();
  await tid('composer-input').waitFor();
  await shot('08-new-chat-empty', 1500);
  await tid('composer-input').fill('Remind me to call Asha tomorrow at 9');
  await tid('composer-send').click();
  await page.waitForTimeout(700);
  await shot('09-thread-working', 300);
  await page.waitForTimeout(20000);
  await shot('10-thread-reply-without-model-key', 500);
};
