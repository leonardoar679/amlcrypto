'use client';

import { useEffect, useState } from 'react';
import AppShell from '../../src/components/layout/AppShell';
import FaqSection from '../../src/components/sections/FaqSection';
import AccountAddressCard from '../../src/components/account/AccountAddressCard';
import AccountRiskPanel from '../../src/components/account/AccountRiskPanel';
import AccountCounterparties from '../../src/components/account/AccountCounterparties';
import AccountLastChecks from '../../src/components/account/AccountLastChecks';
import { getStoredWallet } from '../../src/lib/demoAuth';
import { DEMO_WALLET } from '../../src/lib/demoWallet';
import { faqs } from '../../src/data/siteContent';

const COUNTERPARTIES = [
  { address: '0x0f7ae...13a0e', incoming: 32, outgoing: 0 },
  { address: '0x2abfa...fd57e', incoming: 0, outgoing: 13 },
  { address: '0x39431...2d1bd', incoming: 15, outgoing: 0 },
  { address: '0x74de5...16631', incoming: 6, outgoing: 9 },
  { address: '0xdee52...e8b5e', incoming: 0, outgoing: 5 }
];

// Demonstration rows. The addresses are published test vectors, not real customers' wallets.
const LAST_CHECKS = [
  { date: '15.06.2026 06:40:59', address: '0xde709f2102306220921060314715629080e2fb77', risk: 'Low', percent: 17, pdf: null },
  { date: '08.06.2026 04:27:19', address: '0x27b1fdb04752bbc536007a920d24acb045561c26', risk: 'Low', percent: 11, pdf: '#result' },
  { date: '08.06.2026 04:19:03', address: '0x52908400098527886e0f7030069857d2e4169ee7', risk: 'Low', percent: 15, pdf: null },
  { date: '30.05.2026 19:09:47', address: 'T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb', risk: 'High', percent: 100, pdf: null },
  { date: '29.05.2026 22:41:36', address: 'T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb', risk: 'High', percent: 100, pdf: '#result' }
];

const FLOWS = {
  transactions: {
    incoming: [60.53, 28.37, 11.1],
    outgoing: [47.33, 33.71, 18.96]
  },
  rating: {
    incoming: [45, 35, 20],
    outgoing: [50, 30, 20]
  }
};

function formatCheckDate(date) {
  return `${date.toLocaleDateString('en-US', { month: 'short', day: '2-digit', year: 'numeric' })}, ${date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true })} UTC`;
}

export default function AccountPage() {
  const [address, setAddress] = useState('');
  const [checkDate, setCheckDate] = useState('');

  useEffect(() => {
    const wallet = getStoredWallet();
    setAddress(wallet || DEMO_WALLET);
    setCheckDate(formatCheckDate(new Date()));
  }, []);

  if (!address) {
    return null;
  }

  return (
    <AppShell>
      <div className="account-page">
        <AccountAddressCard
          address={address}
          checkDate={checkDate}
          onUpdate={() => setCheckDate(formatCheckDate(new Date()))}
        />

        <AccountRiskPanel
          incoming={52}
          outgoing={27}
          riskScore={29}
          flows={FLOWS}
          radarValues={[0.72, 0.6, 0.5]}
        />

        <AccountCounterparties
          items={COUNTERPARTIES}
          incomingTotal={{ count: 3, amount: 52 }}
          outgoingTotal={{ count: 3, amount: 27 }}
        />

        <AccountLastChecks items={LAST_CHECKS} />
      </div>

      <FaqSection faqs={faqs} />
    </AppShell>
  );
}
