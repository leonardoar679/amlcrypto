// The product's name and contact details live here and nowhere else. Replace the placeholders when a
// real brand is chosen; any copy that needs the name reads it from this object.
//
// NEXT_PUBLIC_SITE_URL is the public origin, without a trailing slash. Leave it unset until the real
// domain exists: while it is empty the site publishes no canonical or Open Graph URLs.

const siteUrl = (process.env.NEXT_PUBLIC_SITE_URL || '').trim().replace(/\/+$/, '');

export const brand = {
  name: 'AML Platform',
  tagline: 'Blockchain screening, tracing and monitoring',
  description:
    'Screen addresses and transactions, trace the flow of funds and monitor wallets across Ethereum, BNB Smart Chain, TRON, Bitcoin and Solana.',
  siteUrl,
  // example.com is reserved for documentation (RFC 2606), so this can never reach a real mailbox.
  supportEmail: 'contact@example.com',
  // Prefix for every localStorage key and window event this UI uses (see src/lib/storageKeys.js).
  storagePrefix: 'aml'
};

export const supportMailto = `mailto:${brand.supportEmail}`;
