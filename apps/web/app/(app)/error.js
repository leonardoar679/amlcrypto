'use client';

// Shown when a page of the signed-in app throws while rendering. The shell around it stays.
export default function AppError({ reset }) {
  return (
    <div className="app-page">
      <div className="card result-card" role="alert">
        <p className="eyebrow">Something went wrong</p>
        <h1>This page could not be shown</h1>
        <p>An unexpected error stopped the page from loading. Nothing you screened was changed.</p>
        <button className="btn btn-primary" type="button" onClick={reset}>
          Try again
        </button>
      </div>
    </div>
  );
}
