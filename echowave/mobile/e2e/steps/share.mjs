export default async () => {
  await go('/share?url=' + encodeURIComponent('https://www.thehindu.com/business/') + '&text=' + encodeURIComponent('Can you summarise this for me?'));
  await tid('screen-share').waitFor();
  await shot('20-share', 1500);
};
