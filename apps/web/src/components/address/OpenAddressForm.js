'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { api } from '../../lib/api';
import { chainName } from '../../lib/chains';
import { ErrorNotice, Notice } from '../ui/Notice';

// A box that takes an address and goes to a page about it. The platform says which chain(s) the
// address is valid on; when it is valid on several (an EVM address), the person chooses and presses
// the button again. `target(chain, address)` is the path to open.
export default function OpenAddressForm({ target, button = 'Open', initial = '' }) {
  const router = useRouter();
  const [value, setValue] = useState(initial);
  const [candidates, setCandidates] = useState([]);
  const [chain, setChain] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [error, setError] = useState(null);

  const submit = async (event) => {
    event.preventDefault();
    const text = value.trim();
    setProblem('');
    setError(null);
    if (!text) {
      setProblem('Enter an address.');
      return;
    }
    setBusy(true);
    try {
      const found = await api.post('address/validate', { address: text });
      if (!found.valid) {
        setProblem(found.message || 'That is not a valid address on any supported chain.');
        setCandidates([]);
      } else if (found.chain) {
        router.push(target(found.chain, found.normalized || text));
        return;
      } else {
        const options = found.candidates || [];
        const picked = options.find((c) => c.chain === chain);
        if (picked && candidates.length > 0) {
          router.push(target(picked.chain, picked.normalized));
          return;
        }
        setCandidates(options);
        setChain(options[0]?.chain || '');
      }
    } catch (failure) {
      setError(failure);
    }
    setBusy(false);
  };

  return (
    <form className="open-address" onSubmit={submit} noValidate>
      <label className="field open-address-field">
        <span className="sr-only">Address</span>
        <input
          type="text"
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
            setCandidates([]);
          }}
          placeholder="Wallet address"
          autoComplete="off"
          spellCheck={false}
        />
      </label>
      {candidates.length > 0 ? (
        <label className="inline-field">
          On{' '}
          <select value={chain} onChange={(event) => setChain(event.target.value)}>
            {candidates.map((c) => (
              <option key={c.chain} value={c.chain}>
                {chainName(c.chain)}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <button className="btn btn-primary" type="submit" disabled={busy}>
        {busy ? 'Checking...' : candidates.length > 0 ? `${button} on ${chainName(chain)}` : button}
      </button>
      {candidates.length > 0 ? (
        <p className="muted open-address-note">This address is valid on more than one chain. Choose one.</p>
      ) : null}
      {problem ? (
        <Notice tone="bad" title="Check the input">
          {problem}
        </Notice>
      ) : null}
      {error ? <ErrorNotice error={error} title="The address could not be checked" /> : null}
    </form>
  );
}
