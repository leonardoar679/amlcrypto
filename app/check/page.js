'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import AppShell from '../../src/components/layout/AppShell';
import { getStoredQuota, setStoredQuota } from '../../src/lib/demoAuth';
import { SearchIcon } from '../../src/components/icons/Icons';

const HOT_EVENT = {
  label: 'Bybit Exploiter',
  address: '0x47666...486e2',
  fullAddress: '0x47666a35ba0c3d5b4f8e2f8e5b8e3f1a2c3d4e5f486e2'
};

export default function CheckPage() {
  const [walletAddress, setWalletAddress] = useState('');
  const router = useRouter();

  const handleCheck = () => {
    const trimmed = walletAddress.trim();
    if (!trimmed) {
      return;
    }

    const quota = getStoredQuota();
    if (quota > 0) {
      setStoredQuota(quota - 1);
    }
    router.push(`/check/${encodeURIComponent(trimmed)}?source=app`);
  };

  return (
    <AppShell>
      <div className="app-check">
        <div className="app-check-form">
          <input
            type="text"
            placeholder="Check wallet address"
            value={walletAddress}
            onChange={(event) => setWalletAddress(event.target.value)}
          />
          <button type="button" onClick={handleCheck}>
            <SearchIcon width={16} height={16} />
            Check
          </button>
        </div>
        <p className="app-check-hot-event">
          👉 <strong>RECENT HOT EVENT:</strong>{' '}
          <a href={`/check/${encodeURIComponent(HOT_EVENT.fullAddress)}`}>
            {HOT_EVENT.label} ({HOT_EVENT.address})
          </a>
        </p>
      </div>
    </AppShell>
  );
}
