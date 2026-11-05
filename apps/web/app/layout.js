import './globals.css';
import { Inter, Unbounded } from 'next/font/google';
import { brand } from '../src/config/brand';

const inter = Inter({ subsets: ['latin'], variable: '--font-body' });
const unbounded = Unbounded({ subsets: ['latin'], weight: ['500', '700'], variable: '--font-display' });

const title = `${brand.name} | ${brand.tagline}`;

// No canonical or Open Graph URL until NEXT_PUBLIC_SITE_URL names a real domain, and the whole site
// is noindex for now. At launch, allow indexing for the marketing routes only; the signed-in app
// routes must stay noindex, which needs route-group layouts (client pages cannot export metadata).
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

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body className={`${inter.variable} ${unbounded.variable}`}>{children}</body>
    </html>
  );
}
