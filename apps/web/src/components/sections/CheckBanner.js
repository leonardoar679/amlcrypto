'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { brand } from '../../config/brand';
import { supportedChains } from '../../data/siteContent';

// The public page only hands the address to the screening page. Screening needs an account, so a
// visitor who is not signed in is sent to sign in first and comes back to the same address.
export default function CheckBanner({ heading }) {
  const [walletAddress, setWalletAddress] = useState('');
  const [status, setStatus] = useState('');
  const router = useRouter();

  const handleCheck = (event) => {
    event.preventDefault();
    const trimmedAddress = walletAddress.trim();

    if (!trimmedAddress) {
      setStatus('Please enter a wallet address to continue.');
      return;
    }
    router.push(`/check?address=${encodeURIComponent(trimmedAddress)}`);
  };

  return (
    <section id="check" className="check-banner">
      <span className="check-banner-watermark">{brand.name}</span>
      <div className="container check-banner-inner">
        <h2>{heading}</h2>
        <p className="check-banner-sub">
          Recognises addresses on <strong>{supportedChains.join(', ')}</strong>
        </p>
        <form className="check-banner-form" onSubmit={handleCheck}>
          <input
            type="text"
            value={walletAddress}
            onChange={(event) => setWalletAddress(event.target.value)}
            placeholder="Check wallet address"
            aria-label="Wallet address"
            autoComplete="off"
            spellCheck={false}
          />
          <button type="submit">Check</button>
        </form>
        {status ? <p className="check-banner-status">{status}</p> : null}
      </div>
    </section>
  );
}
