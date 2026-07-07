import './globals.css';
import { Inter, Unbounded } from 'next/font/google';

const inter = Inter({ subsets: ['latin'], variable: '--font-body' });
const unbounded = Unbounded({ subsets: ['latin'], weight: ['500', '700'], variable: '--font-display' });

export const metadata = {
  title: 'AML Crypto | Blockchain analysis and crypto incident investigation',
  description:
    'AML Crypto builds tools for analyzing blockchain transactions and investigating crypto incidents: Btrace wallet risk checks, Bholder address graphs, a risk score API, and stolen cryptocurrency recovery.',
  keywords: [
    'AML Crypto',
    'blockchain investigation',
    'crypto wallet check',
    'Btrace',
    'Bholder',
    'risk score API',
    'stolen cryptocurrency recovery'
  ],
  robots: 'index, follow',
  alternates: {
    canonical: 'https://amlcrypto.io/'
  },
  openGraph: {
    title: 'AML Crypto | Blockchain analysis and crypto incident investigation',
    description: 'Tools for analyzing blockchain transactions and investigating crypto incidents.',
    url: 'https://amlcrypto.io/',
    type: 'website'
  },
  twitter: {
    card: 'summary_large_image',
    title: 'AML Crypto | Blockchain analysis and crypto incident investigation',
    description: 'Tools for analyzing blockchain transactions and investigating crypto incidents.'
  }
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body className={`${inter.variable} ${unbounded.variable}`}>{children}</body>
    </html>
  );
}
