'use client';

import { truncateAddress } from '../../lib/demoAuth';
import { AddressBookIcon, WalletGlyphIcon } from '../icons/Icons';

export default function SettingsConnectedAddresses({ address }) {
  return (
    <div className="card settings-connected-card">
      <h2 className="settings-connected-head">
        <AddressBookIcon width={18} height={18} />
        Connected addresses
      </h2>

      <div className="settings-wallet-block">
        <div className="settings-wallet-label">
          <span className="settings-wallet-icon">🦊</span>
          MetaMask
        </div>
        <div className="settings-wallet-value-row">
          <span>{truncateAddress(address, 7, 5)}</span>
          <a href="#disconnect" className="account-address-disconnect">
            Disconnect
          </a>
        </div>
        <button type="button" className="settings-connect-btn">
          Connect one more wallet
        </button>
      </div>

      <div className="settings-wallet-block">
        <div className="settings-wallet-label">
          <WalletGlyphIcon width={20} height={20} className="settings-wallet-icon-glyph" />
          EverWallet
        </div>
        <button type="button" className="settings-connect-btn">
          Connect wallet
        </button>
      </div>
    </div>
  );
}
