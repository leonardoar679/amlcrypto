import Link from 'next/link';

export default function AppNotFound() {
  return (
    <div className="app-page">
      <div className="card result-card">
        <p className="eyebrow">Not found</p>
        <h1>There is nothing at this address</h1>
        <p>The link may be mistyped, or what it pointed to is not part of your organization.</p>
        <Link className="btn btn-primary" href="/check">
          Screen an address
        </Link>
      </div>
    </div>
  );
}
