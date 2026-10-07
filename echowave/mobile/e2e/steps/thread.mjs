export default async () => {
  await go('/');
  await shot('03-chat-threads');
  await tid('thread-7d3c2a10-5b1e-4c8f-9a6d-2f0e8b4c1a99').click();
  await tid('screen-thread').waitFor();
  await shot('04-thread-card-and-connect', 2500);
};
