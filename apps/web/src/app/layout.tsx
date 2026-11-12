import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Outfit, Plus_Jakarta_Sans } from "next/font/google";

import { Analytics } from "@/components/analytics";
import { SiteHeader } from "@/components/site-header";

import "./globals.css";

/**
 * Two faces, self-hosted at build time by next/font: a geometric one for the brand's voice and
 * a readable one for data. Both were chosen partly because they carry real tabular numerals —
 * several rounder alternatives do not, and money columns would quietly stop aligning.
 *
 * Neither is the reference application's own face, which is proprietary and is not used here.
 */
const outfit = Outfit({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-outfit",
  display: "swap",
});

const jakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-jakarta",
  display: "swap",
});

export const metadata: Metadata = {
  title: "CashLens",
  description: "An independent AI CFO for small businesses.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`h-full ${outfit.variable} ${jakarta.variable}`}>
      <body className="flex min-h-full flex-col font-sans antialiased">
        <SiteHeader />
        {children}
        <Analytics />
      </body>
    </html>
  );
}
