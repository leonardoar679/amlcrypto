import './globals.css';
import { Inter } from 'next/font/google';

const inter = Inter({ subsets: ['latin'] });

export const metadata = {
  title: 'AML Cryptocurrency Wallet Check 🛡️ | Check AML wallet for risks for free',
  description:
    'Check whether a crypto wallet is linked to dirty money, sanctions, darknet activity, fraud or high-risk counterparties. Get a free AML wallet check today.',
  keywords: ['AML wallet check', 'crypto wallet check', 'wallet risk score', 'AML screening', 'crypto compliance'],
  robots: 'index, follow',
  alternates: {
    canonical: 'https://btrace.amlcrypto.io/'
  },
  openGraph: {
    title: 'AML Cryptocurrency Wallet Check',
    description: 'Check a crypto wallet for AML risk and uncover suspicious connections before you send funds.',
    url: 'https://btrace.amlcrypto.io/',
    type: 'website'
  },
  twitter: {
    card: 'summary_large_image',
    title: 'AML Cryptocurrency Wallet Check',
    description: 'Protect your funds with a free AML wallet check and risk analysis.'
  }
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body className={inter.className}>{children}</body>
    </html>
  );
}
