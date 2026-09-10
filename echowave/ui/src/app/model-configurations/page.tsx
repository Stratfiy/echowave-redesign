import { redirect } from "next/navigation";

/**
 * Models live on the agent — its Models tab, Simple or Advanced. There is
 * no workspace-level models screen any more; what an agent inherits until
 * it chooses is the managed default and needs no page. The route stays so
 * old links land somewhere useful.
 */
export default function ModelConfigurationsPage() {
    redirect("/workflow");
}
