export default async () => {
  await go('/people');
  await page.waitForTimeout(1500);
  await shot('15-people-consent', 300);
  await tid('people-agree').click();
  await tid('screen-people').waitFor();
  await shot('16-people-synced', 2000);
  await tid('person-demo-1').click();
  await tid('screen-person').waitFor();
  await shot('17-person', 1500);
};
