export default async () => {
  await go('/approvals/3');
  await tid('screen-approval').waitFor();
  await shot('05-approval-detail', 2000);
};
