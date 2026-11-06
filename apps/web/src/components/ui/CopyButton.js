'use client';

import { useEffect, useRef, useState } from 'react';
import { CopyIcon } from '../icons/Icons';

// Copies text to the clipboard. Clipboard access can be refused (an insecure page, a browser
// setting), in which case the button says so instead of pretending.
export default function CopyButton({ text, label = 'Copy' }) {
  const [state, setState] = useState('idle');
  const timer = useRef(null);

  useEffect(() => () => clearTimeout(timer.current), []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState('copied');
    } catch {
      setState('failed');
    }
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState('idle'), 1800);
  };

  return (
    <button className="icon-btn copy-btn" type="button" onClick={copy} aria-label={label} title={label}>
      <CopyIcon width={16} height={16} />
      <span className="copy-state" aria-live="polite">
        {state === 'copied' ? 'Copied' : state === 'failed' ? 'Could not copy' : ''}
      </span>
    </button>
  );
}
