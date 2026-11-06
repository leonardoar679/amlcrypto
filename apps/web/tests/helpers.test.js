import assert from 'node:assert/strict';
import test from 'node:test';
import { chainName, looksLikeTxHash, parseNodeId, sameAddress } from '../src/lib/chains.js';
import { decodeSegment, firstParam, isMalformedPath, safeHref } from '../src/lib/links.js';
import { formatUnits } from '../src/lib/units.js';

test('only ordinary web addresses are followed', () => {
  assert.equal(safeHref('https://sanctionssearch.ofac.treas.gov/Details.aspx?id=1'), 'https://sanctionssearch.ofac.treas.gov/Details.aspx?id=1');
  assert.equal(safeHref('http://example.com/'), 'http://example.com/');
  assert.equal(safeHref('javascript:alert(1)'), null);
  assert.equal(safeHref('JaVaScRiPt:alert(1)'), null);
  assert.equal(safeHref('data:text/html,<script>alert(1)</script>'), null);
  assert.equal(safeHref('//example.com/'), null);
  assert.equal(safeHref('/relative'), null);
  assert.equal(safeHref(''), null);
  assert.equal(safeHref(null), null);
  assert.equal(safeHref(`https://example.com/${'a'.repeat(3000)}`), null);
});

test('a route segment that is not valid percent-encoding is refused, not thrown', () => {
  assert.equal(decodeSegment('0xabc'), '0xabc');
  assert.equal(decodeSegment('a%20b'), 'a b');
  assert.equal(decodeSegment('%'), null);
  assert.equal(decodeSegment('%E0%A4%A'), null);
  assert.equal(decodeSegment(undefined), null);
});

test('the first of several query values is the one used', () => {
  assert.equal(firstParam('a'), 'a');
  assert.equal(firstParam(['a', 'b']), 'a');
  assert.equal(firstParam(undefined), undefined);
});

test('a graph node id names a chain and an address', () => {
  assert.deepEqual(parseNodeId('ethereum:0xabc'), { chain: 'ethereum', address: '0xabc' });
  assert.deepEqual(parseNodeId('tron:TXyz'), { chain: 'tron', address: 'TXyz' });
  assert.equal(parseNodeId('bitcoin:'), null);
  assert.equal(parseNodeId('dogecoin:D123'), null);
  assert.equal(parseNodeId('nocolon'), null);
  assert.equal(parseNodeId(':abc'), null);
  assert.equal(parseNodeId(null), null);
});

test('a transaction hash is told from an address or a truncated paste', () => {
  const hex = 'a'.repeat(64);
  assert.equal(looksLikeTxHash('ethereum', `0x${hex}`), true);
  assert.equal(looksLikeTxHash('bsc', `0x${hex}`), true);
  assert.equal(looksLikeTxHash('ethereum', hex), false); // the 0x is part of an EVM hash
  assert.equal(looksLikeTxHash('ethereum', `0x${hex.slice(1)}`), false);
  assert.equal(looksLikeTxHash('ethereum', `0x${'g'.repeat(64)}`), false);
  assert.equal(looksLikeTxHash('bitcoin', hex), true);
  assert.equal(looksLikeTxHash('tron', hex), true);
  assert.equal(looksLikeTxHash('bitcoin', `0x${hex}`), false);
  assert.equal(looksLikeTxHash('solana', '5'.repeat(88)), true);
  assert.equal(looksLikeTxHash('solana', '0'.repeat(88)), false); // 0 is not in the base58 alphabet
  assert.equal(looksLikeTxHash('dogecoin', hex), false);
  assert.equal(looksLikeTxHash('ethereum', null), false);
});

test('an amount in the smallest unit is shown in whole units, exactly', () => {
  assert.equal(formatUnits('1500000', 6), '1.5');
  assert.equal(formatUnits('1000000000000000000', 18), '1');
  assert.equal(formatUnits('7', 18), '0.000000000000000007');
  assert.equal(formatUnits('0', 18), '0');
  assert.equal(formatUnits('100', 0), '100');
  assert.equal(formatUnits('5', 1), '0.5');
  assert.equal(formatUnits('123456789', 8), '1.23456789');
  assert.equal(formatUnits('00012', 3), '0.012');
  // more digits than a float keeps: still exact
  assert.equal(formatUnits('123456789012345678901234567890', 18), '123456789012.34567890123456789');
  assert.equal(formatUnits(2500000000, 6), '2500');
  // nothing usable: no amount, rather than a wrong one
  assert.equal(formatUnits('-5', 2), null);
  assert.equal(formatUnits('1.5', 2), null);
  assert.equal(formatUnits('abc', 2), null);
  assert.equal(formatUnits('5', null), null);
  assert.equal(formatUnits('5', -1), null);
  assert.equal(formatUnits('5', 1.5), null);
  assert.equal(formatUnits(null, 6), null);
  assert.equal(formatUnits(1e21, 6), null);
});

test('a chain is named from its slug or its CAIP-2 id', () => {
  assert.equal(chainName('bsc'), 'BNB Smart Chain');
  assert.equal(chainName('eip155:56'), 'BNB Smart Chain');
  assert.equal(chainName('eip155:1'), 'Ethereum');
  assert.equal(chainName('tron:728126428'), 'TRON');
  assert.equal(chainName('bip122:000000000019d6689c085ae165831e93'), 'Bitcoin');
  assert.equal(chainName('solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp'), 'Solana');
  assert.equal(chainName('dogecoin'), 'dogecoin');
  assert.equal(chainName(undefined), '-');
});

test('a path with a broken percent sign is told from a good one', () => {
  assert.equal(isMalformedPath('/check/%'), true);
  assert.equal(isMalformedPath('/check/%E0%A4%A'), true);
  assert.equal(isMalformedPath('/addresses/ethereum/%zz'), true);
  assert.equal(isMalformedPath('/check/%25'), false);
  assert.equal(isMalformedPath('/check/scr_1'), false);
  assert.equal(isMalformedPath('/addresses/bitcoin/bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq'), false);
  assert.equal(isMalformedPath('/'), false);
});

test('the same EVM address is the same whatever its case; on other chains case matters', () => {
  assert.equal(sameAddress('ethereum', '0xAbC', '0xabc'), true);
  assert.equal(sameAddress('bsc', '0xAbC', '0xabd'), false);
  assert.equal(sameAddress('bitcoin', 'bc1QAbc', 'bc1qabc'), false);
  assert.equal(sameAddress('solana', 'AbC', 'AbC'), true);
  assert.equal(sameAddress('tron', 'TAbc', 'tabc'), false);
  assert.equal(sameAddress('ethereum', undefined, '0xabc'), false);
  assert.equal(sameAddress('ethereum', '0xabc', null), false);
});
