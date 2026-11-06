'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';

// Reads one thing from the platform and keeps it up to date.
//
//   const { data, error, loading, reload } = useApi('screenings?limit=5');
//
// `path` null means "do not read yet". `refreshMs(data)` returns how many milliseconds to wait
// before reading again, or null to stop: that is how a page follows a job until it is finished.
// After a read that failed because the platform could not be reached (status 0, 5xx) it tries again
// after `retryMs` when that is given; any other error stops the reading.
export function useApi(path, { refreshMs, retryMs } = {}) {
  const [result, setResult] = useState({ path: null, data: null, error: null });
  const [reloads, setReloads] = useState(0);
  const options = useRef({ refreshMs, retryMs });

  useEffect(() => {
    options.current = { refreshMs, retryMs };
  });

  useEffect(() => {
    if (!path) {
      return undefined;
    }
    const controller = new AbortController();
    let timer = null;

    const read = async () => {
      try {
        const data = await api.get(path, { signal: controller.signal });
        if (controller.signal.aborted) {
          return;
        }
        setResult({ path, data, error: null });
        const wait = options.current.refreshMs ? options.current.refreshMs(data) : null;
        if (wait) {
          timer = setTimeout(read, wait);
        }
      } catch (error) {
        if (controller.signal.aborted || error?.name === 'AbortError') {
          return;
        }
        setResult((previous) => ({ path, data: previous.path === path ? previous.data : null, error }));
        const unreachable = error?.status === 0 || error?.status >= 500;
        if (unreachable && options.current.retryMs) {
          timer = setTimeout(read, options.current.retryMs);
        }
      }
    };

    read();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [path, reloads]);

  const reload = useCallback(() => setReloads((count) => count + 1), []);
  const current = result.path === path;
  const data = current ? result.data : null;
  const error = current ? result.error : null;
  return { data, error, loading: Boolean(path) && data === null && error === null, reload };
}
