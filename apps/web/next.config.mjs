import { fileURLToPath } from 'node:url';

// Headers every response carries. The page may not be put in a frame (clickjacking), the browser
// may not guess content types, and a link out of the site does not tell the other site where it
// came from. The Content-Security-Policy is not here: it needs a nonce of its own for every
// request, so proxy.js makes it (src/lib/csp.js).
const securityHeaders = [
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Referrer-Policy', value: 'same-origin' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' }
];

// The container image runs a self-contained server (the pages, and only the modules they use) made
// by building with NEXT_OUTPUT=standalone (see Dockerfile). A plain build, and `next start` with
// it, are unchanged. The tracing root is this directory, so that the server is at the top of the
// output and not under a path of the repository that happens to hold it.
const standalone = process.env.NEXT_OUTPUT === 'standalone';

// The pages use no <Image>, so nothing needs the image optimizer, which is what pulls in sharp and
// its native libvips binaries (LGPL-3.0-or-later) at run time. Left out of the self-contained server,
// they are not shipped.
const withoutImageOptimizer = {
  images: { unoptimized: true },
  outputFileTracingExcludes: { '*': ['node_modules/sharp/**/*', 'node_modules/@img/**/*'] }
};

/** @type {import('next').NextConfig} */
const nextConfig = {
  poweredByHeader: false,
  ...(standalone
    ? { output: 'standalone', outputFileTracingRoot: fileURLToPath(new URL('.', import.meta.url)), ...withoutImageOptimizer }
    : {}),
  async headers() {
    return [{ source: '/:path*', headers: securityHeaders }];
  }
};

export default nextConfig;
