import type { IconName } from "@/components/ui";

/**
 * The demo businesses this application ships and describes.
 *
 * The API answers with every synthetic workspace the database holds, which is the honest
 * answer to "what exists". This is the different question of what the product offers: a
 * development database can also hold a workspace left behind by a check, and a business the
 * interface cannot describe is not something to put in front of a visitor. Both the demo list
 * and the workspace switcher read this, so they never disagree about what is on offer.
 */
export const DEMO_CATALOGUE: Record<string, { icon: IconName; blurb: string }> = {
  "juniper-kitchen": {
    icon: "wallet",
    blurb:
      "Card settlements every few days, weekly supplier deliveries, and a payout that sometimes lands late.",
  },
  "northline-services": {
    icon: "bank",
    blurb: "Lumpy customer receipts, an equipment lease, and a deposit nobody has named yet.",
  },
  "willow-studio": {
    icon: "doc",
    blurb: "Software subscriptions, contractor payments through Venmo, and quieter months.",
  },
};

/** Whether this slug is one the product offers, rather than merely one that exists. */
export const isOfferedDemo = (slug: string) => slug in DEMO_CATALOGUE;
