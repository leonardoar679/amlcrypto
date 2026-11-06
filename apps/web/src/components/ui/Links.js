import Link from 'next/link';
import { addressPath, explorerAddressUrl, explorerTxUrl } from '../../lib/chains';
import { shortAddress } from '../../lib/risk';

// An address: a link to its page here, with a way out to a public explorer. `full` shows the whole
// text (a heading); otherwise a short form, with the whole address in the tooltip.
export function AddressLink({ chain, address, full = false, explorer = true }) {
  if (!address) {
    return <span>-</span>;
  }
  const url = explorer ? explorerAddressUrl(chain, address) : null;
  return (
    <span className="address-link">
      <Link className="mono" href={addressPath(chain, address)} title={address}>
        {full ? address : shortAddress(address)}
      </Link>
      {url ? (
        <a className="explorer-link" href={url} target="_blank" rel="noopener noreferrer" title="Open in a block explorer">
          explorer
          <span className="sr-only"> (opens another site)</span>
        </a>
      ) : null}
    </span>
  );
}

// A transaction hash that opens in a public explorer.
export function TxLink({ chain, hash, full = false }) {
  if (!hash) {
    return <span>-</span>;
  }
  const url = explorerTxUrl(chain, hash);
  const text = full ? hash : shortAddress(hash, 10, 8);
  return url ? (
    <a className="mono" href={url} target="_blank" rel="noopener noreferrer" title={hash}>
      {text}
      <span className="sr-only"> (opens another site)</span>
    </a>
  ) : (
    <span className="mono" title={hash}>
      {text}
    </span>
  );
}
