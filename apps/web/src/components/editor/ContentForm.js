'use client';

import { CHAINS } from '../../lib/chains';
import {
  ADDRESS_ROLES,
  ATTRIBUTION_STATUSES,
  EVIDENCE_KINDS,
  KINDS,
  UPDATE_KINDS,
  attributionLabel,
  blankAddress,
  blankAmount,
  blankEvidence,
  blankUpdate,
  evidenceLabel,
  kindLabel,
  roleLabel,
  screeningNote,
  updateLabel
} from '../../lib/incidents';

// The words the platform uses for each attribution status (it is the same table the public pages
// use), and what each is worth when an editor does not say: the figure a status gives by default.
const DEFAULT_CONFIDENCE = {
  OFFICIALLY_CONFIRMED: 95,
  CORROBORATED: 85,
  REPORTED_BY_ANALYST: 80,
  COMMUNITY_REPORTED: 50,
  INFERRED: 30,
  DISPUTED: 0
};

const today = () => new Date().toISOString().slice(0, 10);
const nowUtc = () => new Date().toISOString().slice(0, 16);

function Text({ label, value, onChange, hint, mono, ...props }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="text" value={value} onChange={(event) => onChange(event.target.value)} className={mono ? 'mono' : undefined} {...props} />
      {hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}

function Area({ label, value, onChange, hint, rows = 3, ...props }) {
  return (
    <label className="field">
      <span>{label}</span>
      <textarea value={value} onChange={(event) => onChange(event.target.value)} rows={rows} {...props} />
      {hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}

function Choice({ label, value, onChange, options, hint }) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}

const chainOptions = (blank) => [...(blank ? [{ value: '', label: blank }] : []), ...CHAINS.map((chain) => ({ value: chain.slug, label: chain.name }))];

function RowBox({ title, onRemove, children }) {
  return (
    <fieldset className="editor-row">
      <legend>{title}</legend>
      {children}
      <button className="btn btn-secondary btn-small" type="button" onClick={onRemove}>
        Remove
      </button>
    </fieldset>
  );
}

// The whole content of an incident as fields. Everything is a string while it is typed; the page
// turns it into what the platform takes (see toBody in src/lib/incidents.js). `words` are the
// platform's own wording for roles and statuses, shown under the choice so an editor reads what the
// public will.
export default function ContentForm({ form, setForm, words }) {
  const set = (field, value) => setForm((current) => ({ ...current, [field]: value }));
  const setRow = (list, index, field, value) =>
    setForm((current) => ({
      ...current,
      [list]: current[list].map((row, at) => (at === index ? { ...row, [field]: value } : row))
    }));
  const add = (list, row) => setForm((current) => ({ ...current, [list]: [...current[list], row] }));
  const remove = (list, index) => setForm((current) => ({ ...current, [list]: current[list].filter((_, at) => at !== index) }));

  const refs = form.evidence.map((item) => ({ value: item.ref, label: `${item.ref}: ${item.publisher || 'a document'}` }));
  const cites = (blank) => [{ value: '', label: blank }, ...refs];

  return (
    <>
      <section className="card inline-form">
        <h2>The incident</h2>
        <Text label="Title" value={form.title} onChange={(value) => set('title', value)} maxLength={200} />
        <div className="form-row">
          <Choice label="Kind" value={form.kind} onChange={(value) => set('kind', value)} options={KINDS.map((kind) => ({ value: kind, label: kindLabel(kind) }))} />
          <label className="field">
            <span>Date it happened</span>
            <input type="date" value={form.occurred_on} onChange={(event) => set('occurred_on', event.target.value)} />
            <span className="field-hint">Needed to publish.</span>
          </label>
        </div>
        <Area
          label="Summary"
          value={form.summary}
          onChange={(value) => set('summary', value)}
          rows={7}
          maxLength={5000}
          hint="What was reported, by whom. Never that an address belongs to an attacker: say who reported it, on the address's line below."
        />
        <Area
          label="How the addresses were attributed (public)"
          value={form.attribution_note}
          onChange={(value) => set('attribution_note', value)}
          rows={3}
          maxLength={2000}
          hint="Shown to everybody under the summary."
        />
        <Area
          label="Note to editors (never public)"
          value={form.internal_note}
          onChange={(value) => set('internal_note', value)}
          rows={3}
          maxLength={2000}
        />
      </section>

      <section className="card inline-form">
        <h2>Documents it rests on ({form.evidence.length})</h2>
        <p className="muted">
          Every amount, every attribution and every timeline entry points at one of these. A short quotation helps; a copied
          article does not belong here.
        </p>
        {form.evidence.map((item, index) => (
          <RowBox key={index} title={`Document ${index + 1}`} onRemove={() => remove('evidence', index)}>
            <div className="form-row">
              <Text label="Name" value={item.ref} onChange={(value) => setRow('evidence', index, 'ref', value)} maxLength={24} mono hint="Letters, digits, - and _." />
              <Choice
                label="What it is"
                value={item.kind}
                onChange={(value) => setRow('evidence', index, 'kind', value)}
                options={EVIDENCE_KINDS.map((kind) => ({ value: kind, label: evidenceLabel(kind) }))}
              />
              <Text label="Published by" value={item.publisher} onChange={(value) => setRow('evidence', index, 'publisher', value)} maxLength={100} />
            </div>
            <Text label="Title" value={item.title} onChange={(value) => setRow('evidence', index, 'title', value)} maxLength={255} />
            <Text label="Link" value={item.url} onChange={(value) => setRow('evidence', index, 'url', value)} maxLength={500} mono placeholder="https://" hint="A web address (http or https)." />
            <div className="form-row">
              <label className="field">
                <span>Published on</span>
                <input type="date" value={item.published_on} onChange={(event) => setRow('evidence', index, 'published_on', event.target.value)} />
              </label>
              <label className="field">
                <span>Read by you on</span>
                <input type="date" value={item.retrieved_on} onChange={(event) => setRow('evidence', index, 'retrieved_on', event.target.value)} />
              </label>
            </div>
            <Area label="Short quotation (optional)" value={item.excerpt} onChange={(value) => setRow('evidence', index, 'excerpt', value)} rows={2} maxLength={500} />
          </RowBox>
        ))}
        <div>
          <button className="btn btn-secondary btn-small" type="button" onClick={() => add('evidence', blankEvidence(form.evidence, today()))}>
            Add a document
          </button>
        </div>
      </section>

      <section className="card inline-form">
        <h2>Amounts reported ({form.amounts.length})</h2>
        <p className="muted">
          One line for each source that reports an amount. Sources disagree, and the page shows each of them; it never adds them up
          or picks one.
        </p>
        {form.amounts.map((item, index) => (
          <RowBox key={index} title={`Amount ${index + 1}`} onRemove={() => remove('amounts', index)}>
            <div className="form-row">
              <Choice
                label="Reported by"
                value={item.evidence_ref}
                onChange={(value) => setRow('amounts', index, 'evidence_ref', value)}
                options={cites('Choose a document')}
              />
              <Text label="Amount (US dollars)" value={item.amount_usd_low} onChange={(value) => setRow('amounts', index, 'amount_usd_low', value)} inputMode="numeric" placeholder="6430000000" />
              <Text label="Up to (if a range)" value={item.amount_usd_high} onChange={(value) => setRow('amounts', index, 'amount_usd_high', value)} inputMode="numeric" />
            </div>
            <div className="form-row">
              <label className="field">
                <span>As of</span>
                <input type="date" value={item.as_of} onChange={(event) => setRow('amounts', index, 'as_of', event.target.value)} />
              </label>
              <Text label="Described as" value={item.label} onChange={(value) => setRow('amounts', index, 'label', value)} maxLength={100} placeholder="An estimate of the funds taken" />
            </div>
          </RowBox>
        ))}
        <div>
          <button className="btn btn-secondary btn-small" type="button" onClick={() => add('amounts', blankAmount(form.evidence))}>
            Add an amount
          </button>
        </div>
      </section>

      <section className="card inline-form">
        <h2>Addresses named ({form.addresses.length})</h2>
        <p className="muted">
          Each address says what part it plays in the story and how firmly that is established, in the platform&apos;s fixed words.
          Screening acts on far fewer of these than the page shows: see the note under an address that it will act on.
        </p>
        {form.addresses.map((item, index) => {
          const note = screeningNote(item.role, item.attribution_status, Boolean(item.evidence_ref));
          return (
            <RowBox key={index} title={`Address ${index + 1}`} onRemove={() => remove('addresses', index)}>
              <div className="form-row">
                <Choice label="Chain" value={item.chain} onChange={(value) => setRow('addresses', index, 'chain', value)} options={chainOptions()} />
                <Text label="Address" value={item.address} onChange={(value) => setRow('addresses', index, 'address', value)} maxLength={512} mono spellCheck="false" />
              </div>
              <div className="form-row">
                <Choice
                  label="Part it plays"
                  value={item.role}
                  onChange={(value) => setRow('addresses', index, 'role', value)}
                  options={ADDRESS_ROLES.map((role) => ({ value: role, label: roleLabel(role) }))}
                  hint={words?.roles?.[item.role]}
                />
                <Choice
                  label="How firmly"
                  value={item.attribution_status}
                  onChange={(value) => setRow('addresses', index, 'attribution_status', value)}
                  options={ATTRIBUTION_STATUSES.map((status) => ({ value: status, label: attributionLabel(status) }))}
                  hint={words?.statuses?.[item.attribution_status]}
                />
                <Text
                  label="Confidence (%)"
                  value={item.confidence}
                  onChange={(value) => setRow('addresses', index, 'confidence', value)}
                  inputMode="numeric"
                  placeholder={String(DEFAULT_CONFIDENCE[item.attribution_status] ?? '')}
                  hint="Leave empty for what the status gives."
                />
              </div>
              <div className="form-row">
                <Choice
                  label="Document that says so"
                  value={item.evidence_ref}
                  onChange={(value) => setRow('addresses', index, 'evidence_ref', value)}
                  options={cites('None')}
                />
                <label className="field">
                  <span>Checked on (UTC)</span>
                  <input type="datetime-local" value={item.last_verified_at} onChange={(event) => setRow('addresses', index, 'last_verified_at', event.target.value)} />
                </label>
              </div>
              <Text label="Note (optional, public)" value={item.note} onChange={(value) => setRow('addresses', index, 'note', value)} maxLength={500} />
              {note ? <p className="editor-warning">{note}</p> : null}
            </RowBox>
          );
        })}
        <div>
          <button className="btn btn-secondary btn-small" type="button" onClick={() => add('addresses', blankAddress())}>
            Add an address
          </button>
        </div>
      </section>

      <section className="card inline-form">
        <h2>Timeline ({form.updates.length})</h2>
        {form.updates.map((item, index) => (
          <RowBox key={index} title={`Entry ${index + 1}`} onRemove={() => remove('updates', index)}>
            <div className="form-row">
              <label className="field">
                <span>When (UTC)</span>
                <input type="datetime-local" value={item.occurred_at} onChange={(event) => setRow('updates', index, 'occurred_at', event.target.value)} />
              </label>
              <Choice
                label="What kind"
                value={item.kind}
                onChange={(value) => setRow('updates', index, 'kind', value)}
                options={UPDATE_KINDS.map((kind) => ({ value: kind, label: updateLabel(kind) }))}
              />
              <Choice label="Source" value={item.evidence_ref} onChange={(value) => setRow('updates', index, 'evidence_ref', value)} options={cites('None')} />
            </div>
            <Area label="What happened" value={item.body} onChange={(value) => setRow('updates', index, 'body', value)} rows={3} maxLength={2000} />
            <div className="form-row">
              <Choice label="Chain (if a transaction)" value={item.chain} onChange={(value) => setRow('updates', index, 'chain', value)} options={chainOptions('None')} />
              <Text label="Transaction hash" value={item.tx_hash} onChange={(value) => setRow('updates', index, 'tx_hash', value)} maxLength={100} mono spellCheck="false" />
            </div>
          </RowBox>
        ))}
        <div>
          <button className="btn btn-secondary btn-small" type="button" onClick={() => add('updates', blankUpdate(nowUtc()))}>
            Add a timeline entry
          </button>
        </div>
      </section>
    </>
  );
}
