/**
 * The workspace named in a link.
 *
 * The API resolves `?workspace=` through the authorized resolver and answers 404 for anything
 * that is not the caller's own, including a value that is not a workspace id at all. The pages
 * follow the same rule: a selection that is present but unusable is reported as unavailable and
 * is never quietly replaced by the account's default business.
 */
const WORKSPACE_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type WorkspaceSelection = {
  /** The link asked for a specific workspace, whether or not the value can be used. */
  present: boolean;
  /** The workspace to load, or null when the link named nothing usable. */
  id: string | null;
};

export function readWorkspaceSelection(
  search:
    | URLSearchParams
    | { get(name: string): string | null; has?(name: string): boolean },
): WorkspaceSelection {
  const raw = search.get("workspace");
  if (raw === null) return { present: false, id: null };
  const trimmed = raw.trim();
  return { present: true, id: WORKSPACE_ID.test(trimmed) ? trimmed : null };
}

/** `?workspace=…` for a known workspace, or nothing at all. */
export function workspaceQuery(id: string | null | undefined): string {
  return id ? `?workspace=${encodeURIComponent(id)}` : "";
}

/** A link that keeps the current selection, for navigation between workspace pages. */
export function withWorkspace(
  path: string,
  id: string | null | undefined,
): string {
  return `${path}${workspaceQuery(id)}`;
}
