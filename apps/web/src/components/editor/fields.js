'use client';

import { CHAINS } from '../../lib/chains';
import { EVIDENCE_KINDS, blankEvidence, evidenceLabel } from '../../lib/incidents';

// The parts of the editor's forms that do not depend on what is being written: a text field, a choice,
// a row with its Remove button, and the documents a page rests on (every page cites some).

export const today = () => new Date().toISOString().slice(0, 10);

export function Text({ label, value, onChange, hint, mono, ...props }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="text" value={value} onChange={(event) => onChange(event.target.value)} className={mono ? 'mono' : undefined} {...props} />
      {hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}

export function Area({ label, value, onChange, hint, rows = 3, ...props }) {
  return (
    <label className="field">
      <span>{label}</span>
      <textarea value={value} onChange={(event) => onChange(event.target.value)} rows={rows} {...props} />
      {hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}

export function Choice({ label, value, onChange, options, hint }) {
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

export const chainOptions = (blank) => [...(blank ? [{ value: '', label: blank }] : []), ...CHAINS.map((chain) => ({ value: chain.slug, label: chain.name }))];

export function RowBox({ title, onRemove, children }) {
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

// The documents of a page, as rows of the form. `setRow`, `add` and `remove` change a list of the form.
export const DOCUMENTS_INTRO =
  'Every amount, every attribution and every timeline entry points at one of these. A short quotation helps; a copied article does not belong here.';

export function DocumentsSection({ form, setRow, add, remove, today: day, intro = DOCUMENTS_INTRO }) {
  return (
    <section className="card inline-form">
      <h2>Documents it rests on ({form.evidence.length})</h2>
      <p className="muted">{intro}</p>
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
        <button className="btn btn-secondary btn-small" type="button" onClick={() => add('evidence', blankEvidence(form.evidence, day))}>
          Add a document
        </button>
      </div>
    </section>
  );
}
