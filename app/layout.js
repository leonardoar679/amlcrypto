import './globals.css';
import { Inter, Unbounded } from 'next/font/google';

const inter = Inter({ subsets: ['latin'], variable: '--font-body' });
const unbounded = Unbounded({ subsets: ['latin'], weight: ['500', '700'], variable: '--font-display' });

export const metadata = {
  title: 'Btrace 🛡️ | Free AML crypto wallet check for risks',
  description:
    'Check whether a crypto wallet is linked to dirty money, sanctions, darknet activity, fraud or high-risk counterparties. Get a free AML wallet check today with Btrace.',
  keywords: ['Btrace', 'AML wallet check', 'crypto wallet check', 'wallet risk score', 'AML screening', 'crypto compliance'],
  robots: 'index, follow',
  alternates: {
    canonical: 'https://btrace.amlcrypto.io/'
  },
  openGraph: {
    title: 'Btrace | AML Cryptocurrency Wallet Check',
    description: 'Check a crypto wallet for AML risk and uncover suspicious connections before you send funds.',
    url: 'https://btrace.amlcrypto.io/',
    type: 'website'
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Btrace | AML Cryptocurrency Wallet Check',
    description: 'Protect your funds with a free AML wallet check and risk analysis.'
  }
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body className={`${inter.variable} ${unbounded.variable}`}>{children}</body>
    </html>
  );
}
