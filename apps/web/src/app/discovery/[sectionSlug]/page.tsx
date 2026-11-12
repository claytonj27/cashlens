"use client";

import { useParams } from "next/navigation";

import { DiscoverySection } from "@/components/discovery-section";
import { DiscoveryShell } from "@/components/discovery-shell";
import { readReviewPersona, useReviewMode } from "@/components/discovery-review";
import { personaLabel, resolveReviewAssignment } from "@/lib/discovery-rules";

export default function DiscoverySectionPage() {
  const params = useParams<{ sectionSlug: string }>();
  const slug = typeof params?.sectionSlug === "string" ? params.sectionSlug : "";
  const review = useReviewMode();
  return (
    <DiscoveryShell>
      {({ me, token }) => {
        // In reviewer mode the chosen persona picks which of the reviewer's OWN assignments the
        // section is read from; otherwise it is the newest, exactly as before. The API resolves
        // the assignment through the session's user id, so nothing else could be reached.
        const target = review ? resolveReviewAssignment(me, readReviewPersona()) : null;
        const assignmentId = target?.assignment_id ?? me.assignment!.assignment_id;
        const previewing = review ? personaLabel(target?.template_slug ?? me.assignment?.template_slug) : null;
        return <DiscoverySection assignmentId={assignmentId} slug={slug} token={token} previewing={previewing} />;
      }}
    </DiscoveryShell>
  );
}
