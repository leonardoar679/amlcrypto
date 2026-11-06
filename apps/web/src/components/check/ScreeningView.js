'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { chainName } from '../../lib/chains';
import { formatWhen, isFinished } from '../../lib/risk';
import { elapsed, rescreenRequest } from '../../lib/screening';
import { useApi } from '../../lib/useApi';
import ReportView from '../risk/ReportView';
import { ErrorNotice, Loading, Notice } from '../ui/Notice';

// How long a screening may wait for a worker before the page says something about it.
const SLOW_QUEUE_SECONDS = 30;

function useSeconds(since, running) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) {
      return undefined;
    }
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  const start = since ? new Date(since).getTime() : now;
  return Math.max(0, (now - start) / 1000);
}

function Rescreen({ screening, label = 'Screen again' }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const { path, body } = rescreenRequest(screening.subject, screening.options);
      const queued = await api.post(path, body);
      router.push(`/check/${encodeURIComponent(queued.screening_id)}`);
    } catch (failure) {
      setError(failure);
      setBusy(false);
    }
  };

  return (
    <>
      <button className="btn btn-primary" type="button" onClick={run} disabled={busy}>
        {busy ? 'Queueing...' : label}
      </button>
      {error ? <ErrorNotice error={error} title="The screening could not be queued" /> : null}
    </>
  );
}

function Waiting({ screening }) {
  const seconds = useSeconds(screening.requested_at, true);
  const queued = screening.status === 'QUEUED';
  const subject = screening.subject;

  return (
    <div className="card result-card waiting-card" role="status" aria-live="polite">
      <p className="eyebrow">
        {subject.type === 'address' ? 'Address screening' : 'Transaction screening'} · {chainName(subject.chain)}
      </p>
      <h1>{queued ? 'Waiting to start' : 'Screening in progress'}</h1>
      <p className="mono wrap-anywhere">{subject.id}</p>
      <div className="loading">
        <span className="spinner" aria-hidden="true" />
        <span>
          {queued ? 'Queued' : 'Reading histories and running the rules'} for {elapsed(seconds)}
        </span>
      </div>
      <p className="muted">
        {subject.type === 'address'
          ? 'The address and its counterparties are read from the data providers (anything already stored is reused), then every rule is run. A busy address can take a few minutes. You can leave this page: the report is kept and listed under Checks history.'
          : 'The transaction is read and each party to it is looked up against the label sources.'}
      </p>
      {queued && seconds > SLOW_QUEUE_SECONDS ? (
        <Notice tone="warn" title="Still waiting for a worker">
          Screenings are run by the background worker. If this stays queued, ask your administrator to check that the worker
          is running.
        </Notice>
      ) : null}
    </div>
  );
}

function Failed({ screening }) {
  return (
    <div className="card result-card">
      <p className="eyebrow">Screening failed</p>
      <h1>This screening could not be completed</h1>
      <p className="mono wrap-anywhere">{screening.subject.id}</p>
      <Notice tone="bad" title="What went wrong">
        {screening.error || 'No reason was recorded.'}
      </Notice>
      <p className="muted">
        Nothing was concluded about this {screening.subject.type}: a failed screening is not a clean result.
      </p>
      <div className="hero-actions">
        <Rescreen screening={screening} label="Try again" />
        <Link className="btn btn-secondary" href="/history">
          Checks history
        </Link>
      </div>
    </div>
  );
}

export default function ScreeningView({ id }) {
  const { data, error, loading, reload } = useApi(`screen/${encodeURIComponent(id)}`, {
    refreshMs: (screening) => (isFinished(screening.status) ? null : 2000),
    retryMs: 5000
  });

  if (loading) {
    return <Loading label="Loading the screening" />;
  }
  if (error && !data) {
    if (error.status === 404) {
      return (
        <div className="card result-card">
          <h1>No such screening</h1>
          <p>There is no screening with that number in your organization.</p>
          <Link className="btn btn-secondary" href="/history">
            Checks history
          </Link>
        </div>
      );
    }
    return <ErrorNotice error={error} onRetry={reload} title="The screening could not be loaded" />;
  }

  if (data.status === 'FAILED') {
    return <Failed screening={data} />;
  }
  if (data.status !== 'COMPLETED' || !data.report) {
    return (
      <>
        <Waiting screening={data} />
        {error ? <ErrorNotice error={error} title="Updating the status failed; retrying" /> : null}
      </>
    );
  }

  return (
    <>
      <ReportView screening={data} />
      <div className="report-footer no-print">
        <p className="muted">
          Requested {formatWhen(data.requested_at)}
          {data.completed_at ? ` · completed ${formatWhen(data.completed_at)}` : ''}
        </p>
        <Rescreen screening={data} />
      </div>
    </>
  );
}
