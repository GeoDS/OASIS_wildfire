import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Wildfire Analyst Agent — User Goal Agent",
  description:
    "Task 1 - compile a natural-language request into a structured Analysis Contract",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="h-full overflow-hidden">{children}</body>
    </html>
  );
}
