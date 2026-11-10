import './globals.css';
import { Inter, Unbounded } from 'next/font/google';
import { headers } from 'next/headers';
import GraphStyle from '../src/components/layout/GraphStyle';
import ThemeScript from '../src/components/layout/ThemeScript';
import { brand } from '../src/config/brand';

const inter = Inter({ subsets: ['latin'], variable: '--font-body' });
const unbounded = Unbounded({ subsets: ['latin'], weight: ['500', '700'], variable: '--font-display' });

const title = `${brand.name} | ${brand.tagline}`;

// No canonical or Open Graph URL until NEXT_PUBLIC_SITE_URL names a real domain, and the whole site
// is noindex for now. At launch, allow indexing for the marketing routes only; the signed-in app
// routes (the (app) group) set their own noindex.
export const metadata = {
  title,
  description: brand.description,
  robots: { index: false, follow: false },
  icons: { icon: '/favicon.svg' },
  ...(brand.siteUrl ? { metadataBase: new URL(brand.siteUrl), alternates: { canonical: '/' } } : {}),
  openGraph: {
    title,
    description: brand.description,
    type: 'website',
    ...(brand.siteUrl ? { url: brand.siteUrl } : {})
  },
  twitter: { card: 'summary', title, description: brand.description }
};

export default async function RootLayout({ children }) {
  // The nonce proxy.js made for this request: the one inline script below may run because it has it.
  // (Reading the request makes every page render for its request, which a nonce needs anyway.)
  const nonce = (await headers()).get('x-nonce') ?? undefined;
  return (
    // The theme script sets data-theme before hydration, so React must keep what is in the DOM.
    <html lang="en" suppressHydrationWarning>
      <head>
        <ThemeScript nonce={nonce} />
        <GraphStyle nonce={nonce} />
      </head>
      <body className={`${inter.variable} ${unbounded.variable}`}>{children}</body>
    </html>
  );
}
