'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { api } from '../../lib/api';
import { KINDS, blankContent, kindLabel, slugFromTitle, toBody, validSlug } from '../../lib/incidents';
import { ErrorNotice, Notice } from '../ui/Notice';
import EditorGate from './EditorGate';

// A new incident starts as a draft with what it takes to name it. The documents it rests on, the
// amounts, the addresses and the timeline are added on the next page, where it can be saved as often
// as needed and nothing is public.
function NewContent() {
  const router = useRouter();
  const [title, setTitle] = useState('');
  const [slug, setSlug] = useState('');
  const [slugEdited, setSlugEdited] = useState(false);
  const [kind, setKind] = useState('hack');
  const [day, setDay] = useState('');
  const [summary, setSummary] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [problem, setProblem] = useState('');

  const address = slugEdited ? slug : slugFromTitle(title);

  const submit = async (event) => {
    event.preventDefault();
    if (busy) {
      return;
    }
    setError(null);
    setProblem('');
    if (!title.trim()) {
      setProblem('Give the incident a title.');
      return;
    }
    if (!summary.trim()) {
      setProblem('Write a short summary. It can be changed for as long as the incident is not published.');
      return;
    }
    if (!validSlug(address)) {
      setProblem('The address of the page is 3 to 80 lowercase letters or digits, joined by single hyphens.');
      return;
    }
    setBusy(true);
    try {
      const body = { ...toBody({ ...blankContent(), title, summary, kind, occurred_on: day }), slug: address };
      const made = await api.post('editor/incidents', body);
      router.push(`/editor/incidents/${made.incident.id}`);
    } catch (failure) {
      setError(failure);
      setBusy(false);
    }
  };

  return (
    <div className="app-page">
      <p>
        <Link href="/editor/incidents">← All incidents</Link>
      </p>
      <h1>New incident</h1>
      <form className="card inline-form" onSubmit={submit} noValidate>
        <label className="field">
          <span>Title</span>
          <input type="text" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} placeholder="What happened, in a few words" />
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
          <span className="field-hint">
            The page will be at /incidents/{address || '...'}. It is permanent: it cannot be changed afterwards.
          </span>
        </label>
        <div className="form-row">
          <label className="field">
            <span>Kind</span>
            <select value={kind} onChange={(event) => setKind(event.target.value)}>
              {KINDS.map((value) => (
                <option key={value} value={value}>
                  {kindLabel(value)}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Date it happened</span>
            <input type="date" value={day} onChange={(event) => setDay(event.target.value)} />
          </label>
        </div>
        <label className="field">
          <span>Summary</span>
          <textarea value={summary} onChange={(event) => setSummary(event.target.value)} rows={5} maxLength={5000} />
          <span className="field-hint">
            Say what was reported, by whom, in the words of a report and not in yours: never that an address is &quot;the
            attacker&apos;s&quot;. Nothing is public yet.
          </span>
        </label>
        {problem ? (
          <Notice tone="warn" title="Not yet">
            {problem}
          </Notice>
        ) : null}
        {error ? <ErrorNotice error={error} title="The incident could not be made" /> : null}
        <div>
          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy ? 'Making...' : 'Make the draft'}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function NewIncident() {
  return (
    <EditorGate>
      <NewContent />
    </EditorGate>
  );
}
