import type { Metadata } from "next";
import "./globals.css";
import { SessionProvider } from "@/components/session";
import { TopBar } from "@/components/top-bar";

export const metadata: Metadata = {
  title: "Legal CMS Onboarding",
  description: "Review, approve and verify CMS onboarding for law firms.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <SessionProvider>
          <TopBar />
          <main>{children}</main>
        </SessionProvider>
      </body>
    </html>
  );
}
