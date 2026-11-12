import { supabaseConfigured } from "@/lib/supabase";

/**
 * Whether this deployment offers product features: demo exploration, accounts
 * and private workspaces. The API is the authority: its product routes answer
 * 404 wherever its FEATURES_ENABLED switch resolves to off, which is the
 * production default. The web app mirrors that from its own build-time
 * configuration so a health-only deployment never advertises features it
 * cannot serve. Production carries no NEXT_PUBLIC_SUPABASE_* values by design;
 * previews and local development set them.
 */
export const featuresConfigured = supabaseConfigured;

/**
 * Whether this deployment publishes the synthetic demo and nothing else.
 *
 * It mirrors the API's own `PUBLIC_DEMO_ONLY`, which is the authority: the API answers 404 for
 * every private path and every workspace that is not a demo. This only stops the interface
 * offering a door that is not there. Hiding the link is never the control.
 */
export const publicDemoOnly = process.env.NEXT_PUBLIC_PUBLIC_DEMO_ONLY === "true";

/** Whether accounts, sign-in and statement uploads are offered at all. */
export const accountsAvailable = featuresConfigured && !publicDemoOnly;

/**
 * Whether the synthetic demo is offered.
 *
 * It is deliberately not gated on the browser Supabase values. Nothing in the demo path signs
 * in: it talks to this application's own API through the proxy and that is all. Requiring the
 * auth keys here would have meant putting them on a public deployment that has no accounts,
 * which is the opposite of what a synthetic-only release is for.
 */
export const demoAvailable = supabaseConfigured || publicDemoOnly;
