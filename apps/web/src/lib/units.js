// An amount the platform holds as a whole number of the asset's smallest unit (wei, satoshi,
// lamport, 10^-6 of a USDT), shown in whole units. Done on the digits: an amount can have more digits
// than a floating-point number keeps, and a rounded amount in a compliance report is a wrong one.

export function formatUnits(raw, decimals) {
  const text = typeof raw === 'number' && Number.isSafeInteger(raw) && raw >= 0 ? String(raw) : raw;
  if (typeof text !== 'string' || !/^\d+$/.test(text)) {
    return null;
  }
  if (!Number.isInteger(decimals) || decimals < 0 || decimals > 77) {
    return null;
  }
  const trimmed = (digits) => digits.replace(/^0+(?=\d)/, '');
  if (decimals === 0) {
    return trimmed(text);
  }
  const padded = text.padStart(decimals + 1, '0');
  const whole = trimmed(padded.slice(0, padded.length - decimals));
  const fraction = padded.slice(padded.length - decimals).replace(/0+$/, '');
  return fraction ? `${whole}.${fraction}` : whole;
}
