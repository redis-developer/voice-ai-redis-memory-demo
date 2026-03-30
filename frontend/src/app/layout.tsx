import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Voice Journal",
  description: "Voice-first journaling with memory-backed chat and recall.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
