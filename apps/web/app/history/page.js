'use client';

import AppShell from '../../src/components/layout/AppShell';
import AppCheckBar from '../../src/components/layout/AppCheckBar';
import AccountLastChecks from '../../src/components/account/AccountLastChecks';

// Demonstration rows. The addresses are published test vectors, not real customers' wallets.
const LAST_CHECKS = [
  { date: '15.06.2026 06:40:59', address: '0xde709f2102306220921060314715629080e2fb77', risk: 'Low', percent: 17, pdf: null },
  { date: '08.06.2026 04:27:19', address: '0x27b1fdb04752bbc536007a920d24acb045561c26', risk: 'Low', percent: 11, pdf: '#result' },
  { date: '08.06.2026 04:19:03', address: '0x52908400098527886e0f7030069857d2e4169ee7', risk: 'Low', percent: 15, pdf: null },
  { date: '30.05.2026 19:09:47', address: 'T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb', risk: 'High', percent: 100, pdf: null },
  { date: '29.05.2026 22:41:36', address: 'T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb', risk: 'High', percent: 100, pdf: '#result' }
];

export default function CheckshistoryPage() {
  return (
    <AppShell>
      <AppCheckBar />

      <div className="account-page">
        <AccountLastChecks items={LAST_CHECKS} heading="My checks" showAllButton={false} />
      </div>
    </AppShell>
  );
}
