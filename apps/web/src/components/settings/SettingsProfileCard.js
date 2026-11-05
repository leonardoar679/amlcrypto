'use client';

import { truncateAddress } from '../../lib/demoAuth';
import { ProfileIcon, EditIcon, MailIcon, TelegramIcon } from '../icons/Icons';

export default function SettingsProfileCard({ address, lastAuthorization }) {
  return (
    <div className="card settings-profile-card">
      <div className="settings-profile-avatar">
        <ProfileIcon width={40} height={40} />
      </div>
      <button type="button" className="settings-profile-replace">
        Replace profile image
      </button>

      <div className="settings-profile-address">
        <span>{truncateAddress(address, 13, 0)}</span>
        <button type="button" className="icon-btn" aria-label="Edit display name">
          <EditIcon width={14} height={14} />
        </button>
      </div>

      <hr className="settings-profile-divider" />

      <p className="settings-profile-label">Last authorization:</p>
      <p className="settings-profile-value">{lastAuthorization}</p>

      <p className="settings-profile-label">Authorization methods:</p>
      <div className="settings-auth-methods">
        <span className="settings-auth-method-icon">
          <MailIcon width={16} height={16} />
        </span>
        <span className="settings-auth-method-icon">
          <TelegramIcon width={16} height={16} />
        </span>
        <span className="settings-auth-method-icon is-active">🦊</span>
      </div>
    </div>
  );
}
