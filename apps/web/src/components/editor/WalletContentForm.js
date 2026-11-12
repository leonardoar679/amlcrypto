'use client';

import {
  ATTRIBUTION_STATUSES,
  DEFAULT_CONFIDENCE,
  attributionLabel
} from '../../lib/incidents';
import { WALLET_KINDS, blankProfileAddress, contextNote, kindHint, kindLabel } from '../../lib/wallets';
import { Area, Choice, DocumentsSection, RowBox, Text, chainOptions, today } from './fields';

// The whole content of a wallet profile as fields. Everything is a string while it is typed; the page
// turns it into what the platform takes (see toBody in src/lib/wallets.js). `words` are the platform's
// own wording for the statuses, shown under the choice so an editor reads what the public will.
export default function WalletContentForm({ form, setForm, words }) {
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
        <h2>The profile</h2>
        <Text label="Name" value={form.name} onChange={(value) => set('name', value)} maxLength={120} />
        <div className="form-row">
          <Choice
            label="Kind"
            value={form.kind}
            onChange={(value) => set('kind', value)}
            options={WALLET_KINDS.map((kind) => ({ value: kind, label: kindLabel(kind) }))}
            hint={kindHint(form.kind) || undefined}
          />
          <Text label="Website (optional)" value={form.website} onChange={(value) => set('website', value)} maxLength={500} mono placeholder="https://" />
        </div>
        <Area
          label="Summary"
          value={form.summary}
          onChange={(value) => set('summary', value)}
          rows={6}
          maxLength={5000}
          hint="Who it is, as its own documents say. Never that an address belongs to somebody on your word: say who reports it, on the address's line below."
        />
        <Area
          label="How the addresses were attributed (public)"
          value={form.attribution_note}
          onChange={(value) => set('attribution_note', value)}
          rows={3}
          maxLength={2000}
          hint="Shown to everybody under the summary."
        />
        <Area label="Note to editors (never public)" value={form.internal_note} onChange={(value) => set('internal_note', value)} rows={3} maxLength={2000} />
      </section>

      <DocumentsSection
        form={form}
        setRow={setRow}
        add={add}
        remove={remove}
        today={today()}
        intro="Every address points at one of these, and a profile cannot be published without them. A short quotation helps; a copied article does not belong here."
      />

      <section className="card inline-form">
        <h2>Addresses named ({form.addresses.length})</h2>
        <p className="muted">
          Each address says what it is and how firmly that is established, in the platform&apos;s fixed words. A profile with no
          address, or an address that cites no document, cannot be published.
        </p>
        {form.addresses.map((item, index) => {
          const note = contextNote(form.kind, item.attribution_status, Boolean(item.evidence_ref));
          const weakFigure = form.kind === 'public_figure' && item.attribution_status !== 'OFFICIALLY_CONFIRMED';
          return (
            <RowBox key={index} title={`Address ${index + 1}`} onRemove={() => remove('addresses', index)}>
              <div className="form-row">
                <Choice label="Chain" value={item.chain} onChange={(value) => setRow('addresses', index, 'chain', value)} options={chainOptions()} />
                <Text label="Address" value={item.address} onChange={(value) => setRow('addresses', index, 'address', value)} maxLength={512} mono spellCheck="false" />
              </div>
              <div className="form-row">
                <Text
                  label="What it is"
                  value={item.label}
                  onChange={(value) => setRow('addresses', index, 'label', value)}
                  maxLength={60}
                  placeholder="Hot wallet, treasury, deposit contract..."
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
                  options={cites('Choose a document')}
                />
                <label className="field">
                  <span>Checked on (UTC)</span>
                  <input type="datetime-local" value={item.last_verified_at} onChange={(event) => setRow('addresses', index, 'last_verified_at', event.target.value)} />
                </label>
              </div>
              <Text label="Note (optional, public)" value={item.note} onChange={(value) => setRow('addresses', index, 'note', value)} maxLength={500} />
              {note ? <p className="editor-warning">{note}</p> : null}
              {weakFigure ? (
                <p className="editor-warning">
                  A public figure&apos;s wallet is published only when it is &quot;officially confirmed&quot;, in a document of the kind
                  &quot;official&quot;.
                </p>
              ) : null}
            </RowBox>
          );
        })}
        <div>
          <button className="btn btn-secondary btn-small" type="button" onClick={() => add('addresses', blankProfileAddress(form.evidence))}>
            Add an address
          </button>
        </div>
      </section>
    </>
  );
}
