import { type Progress } from "@/lib/discovery-api";
import { Chip, ProgressBar } from "@/components/ui";

/** How far the whole assignment, or one section, has got: a bar, the count, and the core. */
export function DiscoveryProgress({
  progress,
  label,
  showCore = true,
}: {
  progress: Progress;
  label: string;
  showCore?: boolean;
}) {
  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-xs">
        <span className="font-medium text-ink">
          {progress.answered} of {progress.total} answered
        </span>
        <span className="flex items-center gap-2 text-ink-muted">
          {showCore ? (
            progress.core_complete ? (
              <Chip tone="up" icon="check">
                Core complete
              </Chip>
            ) : (
              <Chip tone="muted">
                Core {progress.core_answered} of {progress.core_total}
              </Chip>
            )
          ) : null}
          {progress.percent}%
        </span>
      </div>
      <ProgressBar value={progress.answered} max={progress.total} label={label} className="mt-1.5" />
    </div>
  );
}
