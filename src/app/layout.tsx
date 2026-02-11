import { promises as fs } from 'fs';
import path from "path";
import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "@/app/globals.css";
import { AuthProvider } from "@/contexts/auth_context";
import { I18nProvider } from "@/i18n/i18n_context";
import CookieConsent from "@/components/common/cookie_consent";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "SinoBee | Your AI Wealth Nectarist",
  description: "24/7 AI Wealth Nectarist, powered by SinoPac. 智能蜂巢，銀證一體。",
  icons: {
    icon: '/logo.png',
  },
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {

  const privacyPolicyPath = path.join(process.cwd(), 'documents/privacy_policy.md');
  const privacyPolicyContent = await fs.readFile(privacyPolicyPath, 'utf8');

  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <I18nProvider>
          <AuthProvider>
            {children}
            <CookieConsent privacyPolicyContent={privacyPolicyContent} />
          </AuthProvider>
        </I18nProvider>
      </body>
    </html>
  );
}
