import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "FireScope: WildFire Analyst",
  description:
    "Explore fire boundaries, heat anomalies, timelines, and source-backed analysis in one workspace.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="h-full overflow-hidden">{children}</body>
    </html>
  );
}
