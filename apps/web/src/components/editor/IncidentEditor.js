'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { api } from '../../lib/api';
import { chainName } from '../../lib/chains';
import {
  attributionLabel,
  attributionTone,
  blankUpdate,
  evidenceLabel,
  formatDate,
  fromLocalInput,
  kindLabel,
  missingFor,
  roleLabel,
  statusLabel,
  statusTone,
  toBody,
  toForm,
  updateLabel,
  usdRange,
  UPDATE_KINDS
} from '../../lib/incidents';
import { ago, formatWhen, shortAddress } from '../../lib/risk';
import { useApi } from '../../lib/useApi';
import { ErrorNotice, Loading, Notice } from '../ui/Notice';
import ContentForm from './ContentForm';
import { Delete, Move, Outside } from './EditorControls';
import EditorGate from './EditorGate';

const nowUtc = () => new Date().toISOString().slice(0, 16);

// What a published or archived incident holds, to read. It only grows from here: a timeline entry
// is added below, and to change anything else it is taken back to review first.
function ReadOnly({ content }) {
  return (
    <>
      <section className="card">
        <h2>The incident</h2>
        <dl className="facts">
          <div>
            <dt>Kind</dt>
            <dd>{kindLabel(content.kind)}</dd>
          </div>
          <div>
            <dt>Happened</dt>
            <dd>{content.occurred_on ? formatDate(content.occurred_on) : '-'}</dd>
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
        <h2>Documents ({content.evidence.length})</h2>
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
      </section>

      <section className="card">
        <h2>Amounts reported ({content.amounts.length})</h2>
        {content.amounts.length === 0 ? <p className="muted">None.</p> : null}
        {content.amounts.length > 0 ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Reported by</th>
                  <th scope="col">Amount</th>
                  <th scope="col">As of</th>
                  <th scope="col">Described as</th>
                </tr>
              </thead>
              <tbody>
                {content.amounts.map((item, index) => (
                  <tr key={index}>
                    <td className="mono">{item.evidence_ref}</td>
                    <td>{usdRange(item.amount_usd_low, item.amount_usd_high)}</td>
                    <td>{item.as_of ? formatDate(item.as_of) : '-'}</td>
                    <td>{item.label || '-'}</td>
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
                  <th scope="col">Part it plays</th>
                  <th scope="col">How firmly</th>
                  <th scope="col">Source</th>
                </tr>
              </thead>
              <tbody>
                {content.addresses.map((item, index) => (
                  <tr key={index}>
                    <td>
                      <span className="mono">{item.address}</span>
                      <div className="muted small">{chainName(item.chain)}</div>
                    </td>
                    <td>{roleLabel(item.role)}</td>
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

      <section className="card">
        <h2>Funds trail ({(content.flows || []).length})</h2>
        {(content.flows || []).length === 0 ? <p className="muted">None.</p> : null}
        {(content.flows || []).length > 0 ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">From</th>
                  <th scope="col">To</th>
                  <th scope="col">Amount, as reported</th>
                  <th scope="col">Transaction</th>
                  <th scope="col">Source</th>
                </tr>
              </thead>
              <tbody>
                {content.flows.map((item, index) => (
                  <tr key={index}>
                    <td>
                      <span className="mono">{shortAddress(item.from_address, 10, 8)}</span>
                      <div className="muted small">{chainName(item.from_chain)}</div>
                    </td>
                    <td>
                      <span className="mono">{shortAddress(item.to_address, 10, 8)}</span>
                      <div className="muted small">{chainName(item.to_chain)}</div>
                    </td>
                    <td>{item.amount_text || '-'}</td>
                    <td className="mono">{item.tx_hash ? shortAddress(item.tx_hash, 10, 8) : '-'}</td>
                    <td className="mono">{item.evidence_ref}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <section className="card">
        <h2>Timeline ({content.updates.length})</h2>
        {content.updates.length === 0 ? <p className="muted">Nothing yet.</p> : null}
        <ol className="timeline">
          {content.updates.map((item, index) => (
            <li key={index}>
              <span className={`chip ${item.kind === 'correction' ? 'chip-warn' : 'chip-neutral'}`}>{updateLabel(item.kind)}</span>{' '}
              <span className="muted small">{formatWhen(item.occurred_at)}</span>
              <p className="incident-text">{item.body}</p>
            </li>
          ))}
        </ol>
      </section>
    </>
  );
}

// After publication the only change is to add to the timeline: a correction is an entry of kind
// "correction", and what was said before stays where it was.
function AddUpdate({ id, refs, onDone }) {
  const [entry, setEntry] = useState(() => blankUpdate(nowUtc()));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [problem, setProblem] = useState('');
  const set = (field, value) => setEntry((current) => ({ ...current, [field]: value }));

  const submit = async (event) => {
    event.preventDefault();
    setError(null);
    setProblem('');
    const when = fromLocalInput(entry.occurred_at);
    if (!when || !entry.body.trim()) {
      setProblem('Say when it happened and what happened.');
      return;
    }
    setBusy(true);
    try {
      const body = { occurred_at: when, kind: entry.kind, body: entry.body.trim() };
      if (entry.evidence_ref) {
        body.evidence_ref = entry.evidence_ref;
      }
      if (entry.chain) {
        body.chain = entry.chain;
      }
      if (entry.tx_hash.trim()) {
        body.tx_hash = entry.tx_hash.trim();
      }
      const made = await api.post(`editor/incidents/${id}/updates`, body);
      setEntry(blankUpdate(nowUtc()));
      onDone(made.incident);
    } catch (failure) {
      setError(failure);
    }
    setBusy(false);
  };

  return (
    <form className="card inline-form" onSubmit={submit} noValidate>
      <h2>Add to the timeline</h2>
      <p className="muted">
        This is shown to everybody at once. To correct something said before, add an entry of the kind &quot;Correction&quot; that says
        what was wrong; nothing already published is changed or removed.
      </p>
      <div className="form-row">
        <label className="field">
          <span>When (UTC)</span>
          <input type="datetime-local" value={entry.occurred_at} onChange={(event) => set('occurred_at', event.target.value)} />
        </label>
        <label className="field">
          <span>What kind</span>
          <select value={entry.kind} onChange={(event) => set('kind', event.target.value)}>
            {UPDATE_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {updateLabel(kind)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Source</span>
          <select value={entry.evidence_ref} onChange={(event) => set('evidence_ref', event.target.value)}>
            <option value="">None</option>
            {refs.map((ref) => (
              <option key={ref} value={ref}>
                {ref}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="field">
        <span>What happened</span>
        <textarea value={entry.body} onChange={(event) => set('body', event.target.value)} rows={3} maxLength={2000} />
      </label>
      {problem ? (
        <Notice tone="warn" title="Not yet">
          {problem}
        </Notice>
      ) : null}
      {error ? <ErrorNotice error={error} title="That could not be added" /> : null}
      <div>
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? 'Adding...' : 'Add to the timeline'}
        </button>
      </div>
    </form>
  );
}

function Editing({ initial }) {
  const router = useRouter();
  const [incident, setIncident] = useState(initial);
  const [form, setForm] = useState(() => toForm(initial.content));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  const adopt = (next) => {
    setIncident(next);
    setForm(toForm(next.content));
  };
  const dirty = JSON.stringify(toBody(form)) !== JSON.stringify(toBody(toForm(incident.content)));
  const problems = missingFor(form);
  const published = incident.status === 'published';
  const neverPublished = incident.status === 'draft' && !incident.published_at;

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
      const made = await api.put(`editor/incidents/${incident.id}/content`, toBody(form));
      adopt(made.incident);
      setSaved(true);
    });
  const move = (to) =>
    run(async () => {
      const made = await api.post(`editor/incidents/${incident.id}/transition`, { to });
      adopt(made.incident);
    });
  const remove = () =>
    run(async () => {
      await api.del(`editor/incidents/${incident.id}`);
      router.push('/editor/incidents');
    });

  return (
    <div className="app-page">
      <p>
        <Link href="/editor/incidents">← All incidents</Link>
      </p>

      <section className="card">
        <div className="section-title">
          <h1>{incident.title}</h1>
          <span className={`chip ${statusTone(incident.status)}`}>{statusLabel(incident.status)}</span>
        </div>
        <p className="muted small">
          {published ? (
            <>
              Public at <Link href={`/incidents/${incident.slug}`}>/incidents/{incident.slug}</Link>.{' '}
            </>
          ) : (
            <>
              Will be at <span className="mono">/incidents/{incident.slug}</span> when it is published; nobody can read it now.{' '}
            </>
          )}
          Last changed {ago(incident.updated_at)}
          {incident.updated_by?.email ? ` by ${incident.updated_by.email}` : ''}.
        </p>

        {incident.publish_problems.length > 0 && incident.status !== 'archived' ? (
          <Notice tone="warn" title="Before it can be published">
            <ul className="editor-problems">
              {incident.publish_problems.map((text) => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          </Notice>
        ) : null}

        <div className="editor-actions">
          {incident.allowed_transitions.map((to) => (
            <Move
              key={to}
              to={to}
              from={incident.status}
              busy={busy}
              disabled={dirty || (to === 'published' && incident.publish_problems.length > 0)}
              why={dirty ? 'Save your changes first.' : 'It cannot be published yet: see what is missing above.'}
              onMove={move}
            />
          ))}
          {neverPublished ? <Delete busy={busy} onDelete={remove} /> : null}
        </div>
        {dirty ? <p className="muted small">Save your changes before moving the incident.</p> : null}
        {error && !dirty ? <ErrorNotice error={error} title="That could not be done" /> : null}
      </section>

      {incident.editable ? (
        <>
          <ContentForm form={form} setForm={setForm} words={incident.words} />
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
          <Notice tone="info" title="It only grows now">
            {published
              ? 'Everybody can read this incident, so it is not rewritten. Add to the timeline below, or take it back to review to change anything else (it leaves the public pages while it is there).'
              : 'This incident is archived and nobody can read it. Send it back to review to change it.'}
          </Notice>
          <ReadOnly content={incident.content} />
          <AddUpdate id={incident.id} refs={incident.content.evidence.map((item) => item.ref)} onDone={adopt} />
        </>
      )}
    </div>
  );
}

function Load({ id }) {
  const { data, error, loading, reload } = useApi(`editor/incidents/${id}`);
  return (
    <>
      {loading ? <Loading label="Loading the incident" /> : null}
      {error && !data ? (
        <div className="app-page">
          <p>
            <Link href="/editor/incidents">← All incidents</Link>
          </p>
          <ErrorNotice error={error} onRetry={reload} title="The incident could not be loaded" />
        </div>
      ) : null}
      {data ? <Editing key={data.incident.id} initial={data.incident} /> : null}
    </>
  );
}

export default function IncidentEditor({ id }) {
  return (
    <EditorGate>
      <Load id={id} />
    </EditorGate>
  );
}
