'use client';

import { useState } from 'react';
import { needsConfirmation, transitionLabel } from '../../lib/incidents';
import { safeHref } from '../../lib/links';

// The controls the editors of incidents and of wallet profiles share.

// One button that moves a page (an incident or a wallet profile). Publishing and archiving change what everybody sees, so they
// ask a second time; the others do not.
export function Move({ to, from, disabled, why, busy, onMove }) {
  const [sure, setSure] = useState(false);
  const label = transitionLabel(from, to);
  const tone = to === 'published' ? 'btn-primary' : 'btn-secondary';

  if (needsConfirmation(to) && sure) {
    return (
      <span className="confirm">
        {to === 'published' ? 'Show this to everybody?' : 'Take this off the public pages?'}{' '}
        <button className="btn btn-primary btn-small" type="button" disabled={busy} onClick={() => onMove(to)}>
          Yes, {label.toLowerCase()}
        </button>{' '}
        <button className="btn btn-secondary btn-small" type="button" onClick={() => setSure(false)}>
          No
        </button>
      </span>
    );
  }
  return (
    <button
      className={`btn ${tone} btn-small`}
      type="button"
      disabled={disabled || busy}
      title={disabled ? why : undefined}
      onClick={() => (needsConfirmation(to) ? setSure(true) : onMove(to))}
    >
      {label}
    </button>
  );
}

export function Delete({ onDelete, busy }) {
  const [sure, setSure] = useState(false);
  if (!sure) {
    return (
      <button className="btn btn-secondary btn-small" type="button" onClick={() => setSure(true)}>
        Delete this draft
      </button>
    );
  }
  return (
    <span className="confirm">
      Delete it for good?{' '}
      <button className="btn btn-primary btn-small" type="button" disabled={busy} onClick={onDelete}>
        Yes, delete
      </button>{' '}
      <button className="btn btn-secondary btn-small" type="button" onClick={() => setSure(false)}>
        No
      </button>
    </span>
  );
}

export function Outside({ url, children }) {
  const href = safeHref(url);
  return href ? (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
      <span className="sr-only"> (opens another site)</span>
    </a>
  ) : (
    <span>{children}</span>
  );
}
