// The chains the platform reads, and where a person can look a transaction or an address up in a
// public block explorer. The explorer links go to third-party sites, so they carry rel="noopener
// noreferrer" wherever they are rendered.

export const CHAINS = [
  { slug: 'ethereum', id: 'eip155:1', name: 'Ethereum', native: 'ETH' },
  { slug: 'bsc', id: 'eip155:56', name: 'BNB Smart Chain', native: 'BNB' },
  { slug: 'tron', id: 'tron:728126428', name: 'TRON', native: 'TRX' },
  { slug: 'bitcoin', id: 'bip122:000000000019d6689c085ae165831e93', name: 'Bitcoin', native: 'BTC' },
  { slug: 'solana', id: 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', name: 'Solana', native: 'SOL' }
];

// A chain's name from its slug ("bsc") or its CAIP-2 id ("eip155:56"); anything else as it came.
export function chainName(chain) {
  return CHAINS.find((known) => known.slug === chain || known.id === chain)?.name || chain || '-';
}

const EXPLORERS = {
  ethereum: { tx: 'https://etherscan.io/tx/', address: 'https://etherscan.io/address/' },
  bsc: { tx: 'https://bscscan.com/tx/', address: 'https://bscscan.com/address/' },
  tron: { tx: 'https://tronscan.org/#/transaction/', address: 'https://tronscan.org/#/address/' },
  bitcoin: { tx: 'https://blockstream.info/tx/', address: 'https://blockstream.info/address/' },
  solana: { tx: 'https://solscan.io/tx/', address: 'https://solscan.io/account/' }
};

export function explorerTxUrl(chain, hash) {
  const base = EXPLORERS[chain]?.tx;
  return base && hash ? `${base}${encodeURIComponent(hash)}` : null;
}

export function explorerAddressUrl(chain, address) {
  const base = EXPLORERS[chain]?.address;
  return base && address ? `${base}${encodeURIComponent(address)}` : null;
}

// Whether text has the shape of a transaction hash on the chain. This only keeps obvious mistakes
// (an address, a truncated paste) from being queued; whether the transaction exists is the
// platform's to say.
const TX_HASH = {
  ethereum: /^0x[0-9a-fA-F]{64}$/,
  bsc: /^0x[0-9a-fA-F]{64}$/,
  tron: /^[0-9a-fA-F]{64}$/,
  bitcoin: /^[0-9a-fA-F]{64}$/,
  solana: /^[1-9A-HJ-NP-Za-km-z]{86,88}$/
};

export function looksLikeTxHash(chain, text) {
  const shape = TX_HASH[chain];
  return Boolean(shape) && typeof text === 'string' && shape.test(text);
}

// A graph node id is "<chain slug>:<address>". Null when it is anything else.
export function parseNodeId(id) {
  if (typeof id !== 'string') {
    return null;
  }
  const at = id.indexOf(':');
  if (at <= 0) {
    return null;
  }
  const chain = id.slice(0, at);
  const address = id.slice(at + 1);
  return CHAINS.some((known) => known.slug === chain) && address ? { chain, address } : null;
}

// Whether two spellings are the same address on the chain. EVM addresses are the same whatever the
// case (a checksum is only a spelling); on the other chains the case is part of the address.
export function sameAddress(chain, a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') {
    return false;
  }
  return chain === 'ethereum' || chain === 'bsc' ? a.toLowerCase() === b.toLowerCase() : a === b;
}

// The path of the profile page of an address.
export function addressPath(chain, address) {
  return `/addresses/${encodeURIComponent(chain)}/${encodeURIComponent(address)}`;
}

export function graphPath(chain, address) {
  return `/graph?chain=${encodeURIComponent(chain)}&address=${encodeURIComponent(address)}`;
}
