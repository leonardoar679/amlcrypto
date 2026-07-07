'use client';

import { useState } from 'react';
import { truncateAddress } from '../../lib/demoAuth';
import { CopyIcon, RefreshIcon, ChevronDownIcon } from '../icons/Icons';

export default function AccountAddressCard({ address, checkDate, onUpdate }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(address);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="account-page-head">
      <h1>My Addresses</h1>
      <button type="button" className="account-address-select">
        {truncateAddress(address, 7, 5)}
        <ChevronDownIcon width={14} height={14} />
      </button>

      <div className="card account-address-card">
        <div className="account-address-mascot">
          <img src="/img/landing/beast-animation/beast-light.webp" alt="" />
          <span>Connect address</span>
        </div>

        <div className="account-address-details">
          <div className="account-address-row">
            <span className="account-address-label">My address #1</span>
            <span className="account-address-value">{address}</span>
            <button type="button" className="icon-btn" aria-label="Copy address" onClick={handleCopy}>
              <CopyIcon width={16} height={16} />
            </button>
            {copied ? <span className="account-address-copied">Copied</span> : null}
            <a className="account-address-disconnect" href="#disconnect">
              Disconnect
            </a>
          </div>

          <div className="account-address-actions">
            <button type="button" className="btn-update" onClick={onUpdate}>
              <RefreshIcon width={14} height={14} />
              Update
            </button>
            <span className="account-address-date">Check date: {checkDate}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
