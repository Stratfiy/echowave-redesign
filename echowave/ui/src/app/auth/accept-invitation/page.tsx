import { AcceptInvitationForm } from "./AcceptInvitationForm";

// Not prerendered: the form reads the invited address and code off the query
// string, and a build-time render would bake in an empty one.
export const dynamic = "force-dynamic";

export default function AcceptInvitationPage() {
  return <AcceptInvitationForm />;
}
