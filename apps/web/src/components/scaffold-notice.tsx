import { Notice } from "@/components/ui";

/** Shown in place of product features on a health-only deployment. */
export function ScaffoldNotice() {
  return (
    <Notice tone="info">
      This deployment is a health-only scaffold. Demo exploration, accounts and workspaces are not enabled here yet.
    </Notice>
  );
}
