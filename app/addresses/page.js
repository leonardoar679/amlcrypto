'use client';

import { useEffect, useState } from 'react';
import AppShell from '../../src/components/layout/AppShell';
import AppCheckBar from '../../src/components/layout/AppCheckBar';
import AccountAddressCard from '../../src/components/account/AccountAddressCard';
import AccountRiskPanel from '../../src/components/account/AccountRiskPanel';
import AccountCounterparties from '../../src/components/account/AccountCounterparties';
import { getStoredWallet } from '../../src/lib/demoAuth';

const COUNTERPARTIES = [
  { address: '0x0f7ae...13a0e', incoming: 32, outgoing: 0 },
  { address: '0x2abfa...fd57e', incoming: 0, outgoing: 13 },
  { address: '0x39431...2d1bd', incoming: 15, outgoing: 0 },
  { address: '0x74de5...16631', incoming: 6, outgoing: 9 },
  { address: '0xdee52...e8b5e', incoming: 0, outgoing: 5 }
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

export default function AddressesPage() {
  const [address, setAddress] = useState('');
  const [checkDate, setCheckDate] = useState('');

  useEffect(() => {
    const wallet = getStoredWallet();
    setAddress(wallet || '0x45e51367c4395058c7d30ae40355d7f5bf9de1bd');
    setCheckDate(formatCheckDate(new Date()));
  }, []);

  if (!address) {
    return null;
  }

  return (
    <AppShell>
      <AppCheckBar />

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
      </div>
    </AppShell>
  );
}
