'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { CHAINS, chainName, looksLikeTxHash } from '../../lib/chains';
import { formOptions } from '../../lib/screening';
import { ErrorNotice, Notice } from '../ui/Notice';

// Asking for a screening. An address needs its chain: most can only be one, but an EVM address is
// valid on several, so the person picks. Everything is checked with the platform before anything is
// queued, so a mistyped address never becomes a failed job.
export default function CheckForm({ initialAddress = '' }) {
  const router = useRouter();
  const [kind, setKind] = useState('address');
  const [value, setValue] = useState(initialAddress);
  const [txChain, setTxChain] = useState('ethereum');
  const [hint, setHint] = useState(null);
  const [chosen, setChosen] = useState('');
  const [hops, setHops] = useState('2');
  const [days, setDays] = useState('');
  const [update, setUpdate] = useState(true);
  const [refresh, setRefresh] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [problem, setProblem] = useState('');

  // While an address is typed, ask which chain(s) it belongs to. The answer is only a hint: submit
  // asks again.
  useEffect(() => {
    setChosen('');
    const text = value.trim();
    if (kind !== 'address' || text.length < 14) {
      setHint(null);
      return undefined;
    }
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        setHint(await api.post('address/validate', { address: text }, { signal: controller.signal }));
      } catch {
        setHint(null);
      }
    }, 400);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [value, kind]);

  const submit = async (event) => {
    event.preventDefault();
    if (busy) {
      return;
    }
    const text = value.trim();
    setError(null);
    setProblem('');
    if (!text) {
      setProblem(kind === 'address' ? 'Enter an address to screen.' : 'Enter a transaction hash to screen.');
      return;
    }

    setBusy(true);
    try {
      let queued;
      if (kind === 'address') {
        const found = await api.post('address/validate', { address: text });
        if (!found.valid) {
          setProblem(found.message || 'That is not a valid address on any supported chain.');
          setBusy(false);
          return;
        }
        const candidates = found.candidates || [];
        const chain = found.chain || (candidates.some((c) => c.chain === chosen) ? chosen : candidates[0]?.chain);
        const address = found.normalized || candidates.find((c) => c.chain === chain)?.normalized || text;
        queued = await api.post('screen/address', {
          chain,
          address,
          options: formOptions({ hops, days, update, refresh })
        });
      } else {
        if (!looksLikeTxHash(txChain, text)) {
          setProblem(`That does not look like a ${chainName(txChain)} transaction hash.`);
          setBusy(false);
          return;
        }
        queued = await api.post('screen/transaction', { chain: txChain, hash: text });
      }
      router.push(`/check/${encodeURIComponent(queued.screening_id)}`);
    } catch (failure) {
      setError(failure);
      setBusy(false);
    }
  };

  const candidates = hint?.valid && !hint.chain ? hint.candidates || [] : [];
  const selected = candidates.some((c) => c.chain === chosen) ? chosen : candidates[0]?.chain;

  return (
    <form className="card check-card" onSubmit={submit} noValidate>
      <div className="segmented" role="group" aria-label="What to screen">
        <button type="button" className={kind === 'address' ? 'is-active' : ''} aria-pressed={kind === 'address'} onClick={() => setKind('address')}>
          Address
        </button>
        <button
          type="button"
          className={kind === 'transaction' ? 'is-active' : ''}
          aria-pressed={kind === 'transaction'}
          onClick={() => setKind('transaction')}
        >
          Transaction
        </button>
      </div>

      <label className="field">
        <span>{kind === 'address' ? 'Wallet address' : 'Transaction hash'}</span>
        <input
          type="text"
          name="subject"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder={kind === 'address' ? '0x..., bc1..., T..., or a Solana address' : '0x... or a transaction id'}
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
          autoFocus
        />
      </label>

      {kind === 'address' && hint ? (
        <p className={`detect ${hint.valid ? 'detect-ok' : 'detect-bad'}`} role="status">
          {hint.valid && hint.chain ? (
            <>Recognised as a {chainName(hint.chain)} address.</>
          ) : hint.valid ? (
            <>
              Valid on {candidates.map((c) => chainName(c.chain)).join(' and ')}.{' '}
              <label className="inline-field">
                Screen it on{' '}
                <select value={selected} onChange={(event) => setChosen(event.target.value)}>
                  {candidates.map((c) => (
                    <option key={c.chain} value={c.chain}>
                      {chainName(c.chain)}
                    </option>
                  ))}
                </select>
              </label>
            </>
          ) : (
            <>{hint.message || 'Not a valid address.'}</>
          )}
        </p>
      ) : null}

      {kind === 'transaction' ? (
        <label className="field">
          <span>Chain</span>
          <select value={txChain} onChange={(event) => setTxChain(event.target.value)}>
            {CHAINS.map((chain) => (
              <option key={chain.slug} value={chain.slug}>
                {chain.name}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <details className="check-options">
          <summary>Options</summary>
          <div className="check-options-grid">
            <label className="field">
              <span>Hops to follow</span>
              <select value={hops} onChange={(event) => setHops(event.target.value)}>
                <option value="1">1: direct counterparties only</option>
                <option value="2">2: also their counterparties (recommended)</option>
                <option value="3">3: three steps out (reads many histories)</option>
              </select>
              <small className="field-hint">
                With 1 hop the rules that look for indirect exposure cannot run, and the report says so.
              </small>
            </label>
            <label className="field">
              <span>Look back</span>
              <select value={days} onChange={(event) => setDays(event.target.value)}>
                <option value="">Standard</option>
                <option value="30">30 days</option>
                <option value="90">90 days</option>
                <option value="180">180 days</option>
                <option value="365">1 year</option>
                <option value="1095">3 years</option>
              </select>
            </label>
          </div>
          <label className="check-toggle">
            <input type="checkbox" checked={update} onChange={(event) => setUpdate(event.target.checked)} />
            <span>
              Read the data providers first
              <small>
                Recommended. Without it only data already stored is used, which may be missing or old; the report says so.
              </small>
            </span>
          </label>
          {update ? (
            <label className="check-toggle">
              <input type="checkbox" checked={refresh} onChange={(event) => setRefresh(event.target.checked)} />
              <span>
                Read every history again now
                <small>Uses more provider requests. Histories read in the last few minutes are otherwise reused.</small>
              </span>
            </label>
          ) : null}
        </details>
      )}

      {problem ? (
        <Notice tone="bad" title="Check the input">
          {problem}
        </Notice>
      ) : null}
      {error ? <ErrorNotice error={error} title="The screening could not be queued" /> : null}

      <button className="btn btn-primary" type="submit" disabled={busy}>
        {busy ? 'Checking...' : 'Screen'}
      </button>
    </form>
  );
}
