export default async () => {
  await go('/');
  await shot('33-dark-chat', 1500);
  await go('/chat/7d3c2a10-5b1e-4c8f-9a6d-2f0e8b4c1a99');
  await shot('34-dark-thread', 2500);
  await go('/today');
  await shot('35-dark-today', 2000);
};
