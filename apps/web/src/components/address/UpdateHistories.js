'use client';

import { useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';
import { useApi } from '../../lib/useApi';
import { ErrorNotice, Notice } from '../ui/Notice';

const DONE = ['SUCCEEDED', 'FAILED'];

// Reads, through the platform's worker, the histories an address and its counterparties still lack.
// The page never reads from a data provider itself: this queues a job and follows it. Histories
// read recently are reused, so pressing this when nothing is stale costs nothing.
export default function UpdateHistories({
  chain,
  address,
  depth = 1,
  direction = 'both',
  days,
  onDone,
  label = 'Read missing histories'
}) {
  const [jobId, setJobId] = useState(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState(null);
  const reported = useRef(null);

  const { data: job, error: jobError } = useApi(jobId ? `jobs/${jobId}` : null, {
    refreshMs: (current) => (DONE.includes(current.status) ? null : 2000),
    retryMs: 5000
  });

  useEffect(() => {
    if (job && job.status === 'SUCCEEDED' && reported.current !== job.job_id) {
      reported.current = job.job_id;
      if (onDone) {
        onDone(job);
      }
    }
  }, [job, onDone]);

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      const queued = await api.post('graph/update', { chain, address, depth, direction, ...(days ? { days } : {}) });
      setJobId(queued.job_id);
    } catch (failure) {
      setError(failure);
    }
    setStarting(false);
  };

  const running = Boolean(jobId) && !(job && DONE.includes(job.status));

  return (
    <div className="update-histories">
      <button className="btn btn-secondary" type="button" onClick={start} disabled={starting || running}>
        {running ? 'Reading histories...' : label}
      </button>
      {error ? <ErrorNotice error={error} title="This could not be queued" /> : null}
      {jobError && !job ? <ErrorNotice error={jobError} title="The job's state could not be read" /> : null}
      {job && job.status === 'QUEUED' ? (
        <p className="muted">Queued. The background worker picks it up shortly; if it stays queued, check that the worker is running.</p>
      ) : null}
      {job && job.status === 'RUNNING' ? <p className="muted">Reading from the data providers...</p> : null}
      {job && job.status === 'SUCCEEDED' ? <Notice tone="info">The histories are up to date.</Notice> : null}
      {job && job.status === 'FAILED' ? (
        <Notice tone="bad" title="Reading the histories failed">
          {job.error || 'No reason was recorded.'}
        </Notice>
      ) : null}
    </div>
  );
}
