import type { Metadata } from "next";
import { Hanken_Grotesk, Public_Sans, IBM_Plex_Mono } from "next/font/google";
import { resolveLocale } from "./lib/i18n/locale";
import "./globals.css";

const hanken = Hanken_Grotesk({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-hanken",
  display: "swap",
});
const publicSans = Public_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-public-sans",
  display: "swap",
});
const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Unstash",
  description: "Find the right document, fast.",
};

// Applies a saved theme before first paint so the toggle doesn't flash.
const themeInit = `(function(){try{var t=localStorage.getItem('theme');if(t==='dark'||t==='light'){document.documentElement.setAttribute('data-theme',t);}}catch(e){}})();`;

export default async function RootLayout({
  children,
}: {
  readonly children: React.ReactNode;
}) {
  const locale = await resolveLocale();
  return (
    <html
      lang={locale}
      className={`${hanken.variable} ${publicSans.variable} ${plexMono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
