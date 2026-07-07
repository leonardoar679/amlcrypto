'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { getStoredQuota, setStoredQuota } from '../../lib/demoAuth';

export default function CheckBanner({ heading }) {
  const [walletAddress, setWalletAddress] = useState('');
  const [status, setStatus] = useState('');
  const router = useRouter();

  const handleCheck = () => {
    const trimmedAddress = walletAddress.trim();

    if (!trimmedAddress) {
      setStatus('Please enter a wallet address to continue.');
      return;
    }

    const quota = getStoredQuota();
    if (quota <= 0) {
      setStatus('Your free checks are used up for today. Sign in to continue or come back tomorrow.');
      router.push('/auth');
      return;
    }

    setStoredQuota(quota - 1);
    router.push(`/check/${encodeURIComponent(trimmedAddress)}?source=banner`);
  };

  return (
    <section id="check" className="check-banner">
      <span className="check-banner-watermark">Btrace</span>
      <div className="container check-banner-inner">
        <h2>{heading}</h2>
        <p className="check-banner-sub">
          Works with <strong>Ethereum</strong>, <strong>Bitcoin</strong>, <strong>Tron</strong>
        </p>
        <div className="check-banner-form">
          <input
            type="text"
            value={walletAddress}
            onChange={(event) => setWalletAddress(event.target.value)}
            placeholder="Check wallet address"
          />
          <button type="button" onClick={handleCheck}>
            Check
          </button>
        </div>
        {status ? <p className="check-banner-status">{status}</p> : null}
      </div>
    </section>
  );
}
