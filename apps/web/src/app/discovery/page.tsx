"use client";

import { DiscoveryOverview } from "@/components/discovery-overview";
import { DiscoveryShell } from "@/components/discovery-shell";

export default function DiscoveryPage() {
  return <DiscoveryShell>{({ me, token }) => <DiscoveryOverview me={me} token={token} />}</DiscoveryShell>;
}
