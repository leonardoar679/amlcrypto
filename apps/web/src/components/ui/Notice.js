// A short message in a coloured box. `tone`: info, warn or bad. Errors are announced to screen
// readers (role="alert"); the others are not interruptions.
export function Notice({ tone = 'info', title, children, action }) {
  return (
    <div className={`notice notice-${tone}`} role={tone === 'bad' ? 'alert' : 'status'}>
      <div className="notice-body">
        {title ? <strong>{title}</strong> : null}
        {children ? <div>{children}</div> : null}
      </div>
      {action || null}
    </div>
  );
}

// What went wrong reading something, with a way to try again.
export function ErrorNotice({ error, onRetry, title = 'This could not be loaded' }) {
  if (!error) {
    return null;
  }
  return (
    <Notice
      tone="bad"
      title={title}
      action={
        onRetry ? (
          <button className="btn btn-secondary btn-small" type="button" onClick={onRetry}>
            Try again
          </button>
        ) : null
      }
    >
      {error.message || 'The request failed.'}
    </Notice>
  );
}

export function Loading({ label = 'Loading' }) {
  return (
    <div className="loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{label}...</span>
    </div>
  );
}
