export default async () => {
  await go('/reminders/new');
  await tid('reminder-title').fill('Call Asha about the print order');
  await tid('reminder-time').fill('09:00');
  await tid('reminder-check').click();
  await page.waitForTimeout(1500);
  await shot('11-reminder-new-checked', 300);
  await tid('reminder-create').click();
  await tid('screen-reminder').waitFor();
  await shot('12-reminder-detail', 1500);
  await go('/today');
  await tid('screen-today').waitFor();
  await shot('13-today', 2500);
};
