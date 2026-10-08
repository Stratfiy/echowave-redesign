export default async () => {
  await go('/settings');
  await tid('screen-settings').waitFor();
  await page.waitForTimeout(1500);
  await page.evaluate(() => {
    for (const el of document.querySelectorAll('div')) {
      const style = getComputedStyle(el);
      if (el.scrollHeight > el.clientHeight + 50 && /(auto|scroll)/.test(style.overflowY)) el.scrollTop = 1250;
    }
  });
  await shot('22-settings-web-screens', 800);
};
