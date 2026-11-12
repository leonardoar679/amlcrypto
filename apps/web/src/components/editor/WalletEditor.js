'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { api } from '../../lib/api';
import { chainName } from '../../lib/chains';
import { attributionLabel, attributionTone, evidenceLabel, formatDate, statusLabel, statusTone } from '../../lib/incidents';
import { ago, shortAddress } from '../../lib/risk';
import { useApi } from '../../lib/useApi';
import { kindLabel, missingFor, toBody, toForm } from '../../lib/wallets';
import { ErrorNotice, Loading, Notice } from '../ui/Notice';
import { Delete, Move, Outside } from './EditorControls';
import EditorGate from './EditorGate';
import WalletContentForm from './WalletContentForm';

// What a published or archived profile holds, to read. To change it, it is taken back to review first
// (it leaves the public pages, and what screening took from it, while it is there).
function ReadOnly({ content }) {
  return (
    <>
      <section className="card">
        <h2>The profile</h2>
        <dl className="facts">
          <div>
            <dt>Kind</dt>
            <dd>{kindLabel(content.kind)}</dd>
          </div>
          <div>
            <dt>Website</dt>
            <dd>{content.website ? <Outside url={content.website}>{content.website}</Outside> : '-'}</dd>
          </div>
        </dl>
        <h3>Summary</h3>
        <p className="incident-text">{content.summary}</p>
        {content.attribution_note ? (
          <>
            <h3>How the addresses were attributed (public)</h3>
            <p className="incident-text">{content.attribution_note}</p>
          </>
        ) : null}
        {content.internal_note ? (
          <>
            <h3>Note to editors (never public)</h3>
            <p className="incident-text">{content.internal_note}</p>
          </>
        ) : null}
      </section>

      <section className="card">
        <h2>Documents it rests on ({content.evidence.length})</h2>
        {content.evidence.length === 0 ? <p className="muted">None.</p> : null}
        {content.evidence.length > 0 ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Published by</th>
                  <th scope="col">Title</th>
                  <th scope="col">What it is</th>
                  <th scope="col">Read on</th>
                </tr>
              </thead>
              <tbody>
                {content.evidence.map((item) => (
                  <tr key={item.ref}>
                    <td className="mono">{item.ref}</td>
                    <td>{item.publisher}</td>
                    <td>
                      <Outside url={item.url}>{item.title}</Outside>
                    </td>
                    <td>{evidenceLabel(item.kind)}</td>
                    <td>{formatDate(item.retrieved_on)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <section className="card">
        <h2>Addresses named ({content.addresses.length})</h2>
        {content.addresses.length === 0 ? <p className="muted">None.</p> : null}
        {content.addresses.length > 0 ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Address</th>
                  <th scope="col">What it is</th>
                  <th scope="col">How firmly</th>
                  <th scope="col">Source</th>
                </tr>
              </thead>
              <tbody>
                {content.addresses.map((item, index) => (
                  <tr key={index}>
                    <td>
                      <span className="mono" title={item.address}>
                        {shortAddress(item.address, 12, 8)}
                      </span>
                      <div className="muted small">{chainName(item.chain)}</div>
                    </td>
                    <td>{item.label}</td>
                    <td>
                      <span className={`chip ${attributionTone(item.attribution_status)}`}>{attributionLabel(item.attribution_status)}</span>{' '}
                      <span className="muted small">{item.confidence}%</span>
                    </td>
                    <td className="mono">{item.evidence_ref || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </>
  );
}

function Editing({ initial }) {
  const router = useRouter();
  const [profile, setProfile] = useState(initial);
  const [form, setForm] = useState(() => toForm(initial.content));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  const adopt = (next) => {
    setProfile(next);
    setForm(toForm(next.content));
  };
  const dirty = JSON.stringify(toBody(form)) !== JSON.stringify(toBody(toForm(profile.content)));
  const problems = missingFor(form);
  const published = profile.status === 'published';
  const neverPublished = profile.status === 'draft' && !profile.published_at;

  const run = async (work) => {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await work();
    } catch (failure) {
      setError(failure);
    }
    setBusy(false);
  };

  const save = () =>
    run(async () => {
      const made = await api.put(`editor/wallets/${profile.id}/content`, toBody(form));
      adopt(made.wallet);
      setSaved(true);
    });
  const move = (to) =>
    run(async () => {
      const made = await api.post(`editor/wallets/${profile.id}/transition`, { to });
      adopt(made.wallet);
    });
  const remove = () =>
    run(async () => {
      await api.del(`editor/wallets/${profile.id}`);
      router.push('/editor/wallets');
    });

  return (
    <div className="app-page">
      <p>
        <Link href="/editor/wallets">← All wallet profiles</Link>
      </p>

      <section className="card">
        <div className="section-title">
          <h1>{profile.name}</h1>
          <span className={`chip ${statusTone(profile.status)}`}>{statusLabel(profile.status)}</span>
        </div>
        <p className="muted small">
          {published ? (
            <>
              Public at <Link href={`/wallets/${profile.slug}`}>/wallets/{profile.slug}</Link>.{' '}
            </>
          ) : (
            <>
              Will be at <span className="mono">/wallets/{profile.slug}</span> when it is published; nobody can read it now.{' '}
            </>
          )}
          Last changed {ago(profile.updated_at)}
          {profile.updated_by?.email ? ` by ${profile.updated_by.email}` : ''}.
        </p>

        {profile.publish_problems.length > 0 && profile.status !== 'archived' ? (
          <Notice tone="warn" title="Before it can be published">
            <ul className="editor-problems">
              {profile.publish_problems.map((text) => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          </Notice>
        ) : null}

        <div className="editor-actions">
          {profile.allowed_transitions.map((to) => (
            <Move
              key={to}
              to={to}
              from={profile.status}
              busy={busy}
              disabled={dirty || (to === 'published' && profile.publish_problems.length > 0)}
              why={dirty ? 'Save your changes first.' : 'It cannot be published yet: see what is missing above.'}
              onMove={move}
            />
          ))}
          {neverPublished ? <Delete busy={busy} onDelete={remove} /> : null}
        </div>
        {dirty ? <p className="muted small">Save your changes before moving the profile.</p> : null}
        {error && !dirty ? <ErrorNotice error={error} title="That could not be done" /> : null}
      </section>

      {profile.editable ? (
        <>
          <WalletContentForm form={form} setForm={setForm} words={profile.words} />
          <section className="card inline-form editor-save">
            {problems.length > 0 ? (
              <Notice tone="warn" title="Before it can be saved">
                <ul className="editor-problems">
                  {problems.map((text) => (
                    <li key={text}>{text}</li>
                  ))}
                </ul>
              </Notice>
            ) : null}
            {error && dirty ? <ErrorNotice error={error} title="It could not be saved" /> : null}
            {saved && !dirty ? <Notice tone="info">Saved. Nothing is public until you publish it.</Notice> : null}
            <div>
              <button className="btn btn-primary" type="button" disabled={busy || !dirty || problems.length > 0} onClick={save}>
                {busy ? 'Saving...' : 'Save'}
              </button>{' '}
              {dirty ? <span className="muted small">There are changes that are not saved.</span> : null}
            </div>
          </section>
        </>
      ) : (
        <>
          <Notice tone="info" title="Take it back to review to change it">
            {published
              ? 'Everybody can read this profile, so it is not rewritten in place. Taking it back to review removes it from the public pages, and what screening took from it, until it is published again.'
              : 'This profile is archived and nobody can read it. Send it back to review to change it.'}
          </Notice>
          <ReadOnly content={profile.content} />
        </>
      )}
    </div>
  );
}

function Load({ id }) {
  const { data, error, loading, reload } = useApi(`editor/wallets/${id}`);
  return (
    <>
      {loading ? <Loading label="Loading the profile" /> : null}
      {error && !data ? (
        <div className="app-page">
          <p>
            <Link href="/editor/wallets">← All wallet profiles</Link>
          </p>
          <ErrorNotice error={error} onRetry={reload} title="The profile could not be loaded" />
        </div>
      ) : null}
      {data ? <Editing key={data.wallet.id} initial={data.wallet} /> : null}
    </>
  );
}

export default function WalletEditor({ id }) {
  return (
    <EditorGate>
      <Load id={id} />
    </EditorGate>
  );
}
