import type { Metadata, Viewport } from "next";
import "@fontsource-variable/bricolage-grotesque";
import "@fontsource/atkinson-hyperlegible/400.css";
import "@fontsource/atkinson-hyperlegible/700.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Day Manager",
  description: "Your day, your plan, your progress.",
  manifest: "/manifest.webmanifest",
  icons: { icon: "/icon.svg", apple: "/icon.svg" },
  appleWebApp: { capable: true, title: "Day", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  themeColor: "#1E2A36",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-GB">
      <body className="min-h-dvh antialiased">{children}</body>
    </html>
  );
}
