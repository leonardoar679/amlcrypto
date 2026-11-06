import { redirect } from 'next/navigation';
import SignInForm from '@/components/auth/SignInForm';
import { brand } from '@/config/brand';
import { safeNext } from '@/lib/safeNext';
import { getSession } from '@/server/session';

export const metadata = {
  title: 'Sign in',
  robots: { index: false, follow: false }
};

function first(value) {
  return Array.isArray(value) ? value[0] : value;
}

export default async function AuthPage({ searchParams }) {
  const params = await searchParams;
  const next = safeNext(first(params?.next));
  const session = await getSession();

  // Already signed in: there is nothing to do here.
  if (session.state === 'signed-in') {
    redirect(next);
  }

  return (
    <main className="result-shell">
      <div className="container">
        <div className="card result-card auth-card">
          <p className="eyebrow">{brand.name}</p>
          <h1>Sign in</h1>
          <p>Accounts are created by your organization&apos;s administrator. Sign in with the email address and password you were given.</p>
          <SignInForm next={next} expired={first(params?.expired) === '1'} unavailable={session.state === 'unavailable'} />
        </div>
      </div>
    </main>
  );
}
