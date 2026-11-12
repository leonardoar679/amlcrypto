'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { api } from '../../lib/api';
import { slugFromTitle, validSlug } from '../../lib/incidents';
import { WALLET_KINDS, blankProfile, kindHint, kindLabel, toBody } from '../../lib/wallets';
import { ErrorNotice, Notice } from '../ui/Notice';
import EditorGate from './EditorGate';

// A new profile starts as a draft with what it takes to name it. The documents it rests on and the
// addresses are added on the next page, where it can be saved as often as needed and nothing is public.
function NewContent() {
  const router = useRouter();
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [slugEdited, setSlugEdited] = useState(false);
  const [kind, setKind] = useState('exchange');
  const [summary, setSummary] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [problem, setProblem] = useState('');

  const address = slugEdited ? slug : slugFromTitle(name);

  const submit = async (event) => {
    event.preventDefault();
    if (busy) {
      return;
    }
    setError(null);
    setProblem('');
    if (!name.trim()) {
      setProblem('Give the profile a name.');
      return;
    }
    if (!summary.trim()) {
      setProblem('Write a short summary. It can be changed for as long as the profile is not published.');
      return;
    }
    if (!validSlug(address)) {
      setProblem('The address of the page is 3 to 80 lowercase letters or digits, joined by single hyphens.');
      return;
    }
    setBusy(true);
    try {
      const body = { ...toBody({ ...blankProfile(), name, summary, kind }), slug: address };
      const made = await api.post('editor/wallets', body);
      router.push(`/editor/wallets/${made.wallet.id}`);
    } catch (failure) {
      setError(failure);
      setBusy(false);
    }
  };

  return (
    <div className="app-page">
      <p>
        <Link href="/editor/wallets">← All wallet profiles</Link>
      </p>
      <h1>New wallet profile</h1>
      <form className="card inline-form" onSubmit={submit} noValidate>
        <label className="field">
          <span>Name</span>
          <input type="text" value={name} onChange={(event) => setName(event.target.value)} maxLength={120} placeholder="An exchange, a protocol, a fund..." />
          <span className="field-hint">An organization or a protocol, or a public figure who has disclosed their own wallet. Never a private person.</span>
        </label>
        <label className="field">
          <span>Address of the page</span>
          <input
            type="text"
            value={address}
            onChange={(event) => {
              setSlug(event.target.value);
              setSlugEdited(true);
            }}
            maxLength={80}
            spellCheck="false"
            className="mono"
          />
          <span className="field-hint">The page will be at /wallets/{address || '...'}. It is permanent: it cannot be changed afterwards.</span>
        </label>
        <label className="field">
          <span>Kind</span>
          <select value={kind} onChange={(event) => setKind(event.target.value)}>
            {WALLET_KINDS.map((value) => (
              <option key={value} value={value}>
                {kindLabel(value)}
              </option>
            ))}
          </select>
          {kindHint(kind) ? <span className="field-hint">{kindHint(kind)}</span> : null}
        </label>
        <label className="field">
          <span>Summary</span>
          <textarea value={summary} onChange={(event) => setSummary(event.target.value)} rows={5} maxLength={5000} />
          <span className="field-hint">Who it is, in the words of its own documents and not in yours. Nothing is public yet.</span>
        </label>
        {problem ? (
          <Notice tone="warn" title="Not yet">
            {problem}
          </Notice>
        ) : null}
        {error ? <ErrorNotice error={error} title="The profile could not be made" /> : null}
        <div>
          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy ? 'Making...' : 'Make the draft'}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function NewWallet() {
  return (
    <EditorGate>
      <NewContent />
    </EditorGate>
  );
}
