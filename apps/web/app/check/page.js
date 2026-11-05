'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import AppShell from '../../src/components/layout/AppShell';
import { getStoredQuota, setStoredQuota } from '../../src/lib/demoAuth';
import { SearchIcon } from '../../src/components/icons/Icons';
import { supportedChains } from '../../src/data/siteContent';

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
        <p className="app-check-note">
          Paste an address from {supportedChains.join(', ')}. Results in this build are demonstration data.
        </p>
      </div>
    </AppShell>
  );
}
