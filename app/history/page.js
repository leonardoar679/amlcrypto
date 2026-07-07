'use client';

import AppShell from '../../src/components/layout/AppShell';
import AppCheckBar from '../../src/components/layout/AppCheckBar';
import AccountLastChecks from '../../src/components/account/AccountLastChecks';

const LAST_CHECKS = [
  { date: '15.06.2026 06:40:59', address: '0xd5f5df7c9588673845ab815c3064c75b945d614a', risk: 'Low', percent: 17, pdf: null },
  { date: '08.06.2026 04:27:19', address: '0x5d76a92b7cb9e1a81b8eb8c16468f1155b2f64f4', risk: 'Low', percent: 11, pdf: '#result' },
  { date: '08.06.2026 04:19:03', address: '0xc2a47582215b740734ce1f2342ec7d149143ea61', risk: 'Low', percent: 15, pdf: null },
  { date: '30.05.2026 19:09:47', address: 'TDGZ4GfvCRe1d8oksj8fBD77ZHw4bkCPBA', risk: 'High', percent: 100, pdf: null },
  { date: '29.05.2026 22:41:36', address: 'TDGZ4GfvCRe1d8oksj8fBD77ZHw4bkCPBA', risk: 'High', percent: 100, pdf: '#result' }
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
