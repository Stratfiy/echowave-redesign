export default async () => {
  await go('/chat/665b6e2e-f0d8-4ac9-9379-a574679dbe57');
  await tid('composer-attach').click();
  await page.waitForTimeout(500);
  const chooser = page.waitForEvent('filechooser');
  await tid('attach-files').click();
  (await chooser).setFiles('./print-order.txt');
  await page.waitForTimeout(6000);
  await tid('composer-input').fill('What does this order say?');
  await shot('14-attachment-ready', 500);
};
