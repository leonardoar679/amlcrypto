import { redirect } from 'next/navigation';
import AppShell from '@/components/layout/AppShell';
import { getSession } from '@/server/session';

// The signed-in part of the site: not indexed, and shown only to someone the backend recognises.
// (This check is for the person's benefit. What protects the data is that every request for it goes
// to the backend, which wants a valid token each time.)
export const metadata = {
  title: 'Dashboard',
  robots: { index: false, follow: false }
};

export default async function AppLayout({ children }) {
  const session = await getSession();

  if (session.state === 'anonymous') {
    redirect('/auth');
  }
  if (session.state === 'expired') {
    redirect('/api/session/expired');
  }
  if (session.state === 'unavailable') {
    return (
      <main className="result-shell">
        <div className="container">
          <div className="card result-card" role="alert">
            <p className="eyebrow">Service unavailable</p>
            <h1>The platform cannot be reached</h1>
            <p>
              This site could not get an answer from the platform&apos;s API. Nothing is lost: try again in a
              moment.
            </p>
            <a className="btn btn-primary" href="">
              Try again
            </a>
          </div>
        </div>
      </main>
    );
  }

  return (
    <AppShell user={session.user} organization={session.organization} role={session.role} permissions={session.permissions}>
      {children}
    </AppShell>
  );
}
