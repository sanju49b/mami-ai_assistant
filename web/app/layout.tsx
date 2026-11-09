import type { Metadata, Viewport } from "next";
import "@fontsource-variable/source-serif-4";
import "@fontsource-variable/instrument-sans";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mami",
  description: "Mami, your day manager.",
  manifest: "/manifest.webmanifest",
  icons: { icon: "/icon.svg", apple: "/icon.svg" },
  appleWebApp: { capable: true, title: "Mami", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  themeColor: "#F6F3EE",
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
