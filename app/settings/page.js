'use client';

import { useEffect, useState } from 'react';
import AppShell from '../../src/components/layout/AppShell';
import AppCheckBar from '../../src/components/layout/AppCheckBar';
import SettingsProfileCard from '../../src/components/settings/SettingsProfileCard';
import SettingsForm from '../../src/components/settings/SettingsForm';
import SettingsConnectedAddresses from '../../src/components/settings/SettingsConnectedAddresses';
import { getStoredWallet } from '../../src/lib/demoAuth';

function formatAuthTimestamp(date) {
  return `${date.toLocaleDateString('en-GB').replace(/\//g, '.')} ${date.toLocaleTimeString('en-GB')}`;
}

export default function SettingsPage() {
  const [address, setAddress] = useState('');
  const [lastAuthorization, setLastAuthorization] = useState('');

  useEffect(() => {
    setAddress(getStoredWallet() || '0x45e51367c4395058c7d30ae40355d7f5bf9de1bd');
    setLastAuthorization(formatAuthTimestamp(new Date()));
  }, []);

  if (!address) {
    return null;
  }

  return (
    <AppShell>
      <AppCheckBar />

      <div className="settings-page">
        <h1>Account settings</h1>

        <div className="settings-grid">
          <SettingsProfileCard address={address} lastAuthorization={lastAuthorization} />
          <SettingsForm />
          <SettingsConnectedAddresses address={address} />
        </div>
      </div>
    </AppShell>
  );
}
