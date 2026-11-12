import Link from 'next/link';
import PublicShell from '@/components/layout/PublicShell';

// Whether a profile does not exist or is not published, the answer is the same: it is not here.
export default function WalletNotFound() {
  return (
    <PublicShell>
      <main id="top" className="incident-page">
        <div className="container">
          <div className="card result-card">
            <p className="eyebrow">Known wallets</p>
            <h1>No such profile</h1>
            <p>There is no published wallet profile at this address. It may have been withdrawn, or the link may be wrong.</p>
            <Link className="btn btn-primary" href="/wallets">
              All wallet profiles
            </Link>
          </div>
        </div>
      </main>
    </PublicShell>
  );
}
