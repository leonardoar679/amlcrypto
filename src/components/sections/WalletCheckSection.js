'use client';

import { useState } from 'react';

export default function WalletCheckSection() {
  const [walletAddress, setWalletAddress] = useState('');
  const [status, setStatus] = useState('');

  const handleCheck = () => {
    if (!walletAddress.trim()) {
      setStatus('Please enter a wallet address to continue.');
      return;
    }

    setStatus(`Checking ${walletAddress.trim()}... This demo will return a risk summary shortly.`);
  };

  return (
    <section id="check" className="section section-alt">
      <div className="container">
        <div className="section-heading">
          <p className="eyebrow">Free check</p>
          <h2>Check crypto wallet in 3 seconds. Free.</h2>
          <p>Works with Ethereum, Bitcoin and Tron.</p>
        </div>
        <div className="search-card card">
          <input
            type="text"
            value={walletAddress}
            onChange={(event) => setWalletAddress(event.target.value)}
            placeholder="Check wallet address"
          />
          <button className="btn btn-primary" onClick={handleCheck}>
            Check
          </button>
        </div>
        {status ? <p className="status-text">{status}</p> : null}
      </div>
    </section>
  );
}
