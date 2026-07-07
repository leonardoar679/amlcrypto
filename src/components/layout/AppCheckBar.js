'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { getStoredQuota, setStoredQuota } from '../../lib/demoAuth';
import { SearchIcon } from '../icons/Icons';

export default function AppCheckBar() {
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
    <div className="app-check-bar">
      <div className="app-check-bar-form">
        <input
          type="text"
          placeholder="Check wallet address"
          value={walletAddress}
          onChange={(event) => setWalletAddress(event.target.value)}
          onKeyDown={(event) => event.key === 'Enter' && handleCheck()}
        />
        <button type="button" onClick={handleCheck}>
          <SearchIcon width={16} height={16} />
          Check
        </button>
      </div>
    </div>
  );
}
