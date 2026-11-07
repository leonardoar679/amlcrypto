'use client';

import { useCan } from '../layout/SessionContext';
import { Notice } from '../ui/Notice';
import SettingsTabs from './SettingsTabs';

// What a section of Settings that only some roles may use shows to everyone else who reaches it by
// its address: a plain explanation, not a form that the platform would refuse. The section's own
// component is only mounted for someone allowed, so it asks the platform for nothing otherwise. The
// platform refuses what a role may not do whatever this page shows.
export default function Restricted({ permission, what, children }) {
  if (useCan(permission)) {
    return children;
  }
  return (
    <div className="app-page">
      <h1>Settings</h1>
      <SettingsTabs />
      <Notice tone="info" title="Not for your role">
        {what} is for admins. Ask an admin of your organization if you need it.
      </Notice>
    </div>
  );
}
