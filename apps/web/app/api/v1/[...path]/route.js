import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';
import { backendFetch } from '@/server/backend';
import {
  MAX_BODY_BYTES,
  cleanPath,
  forwardedRequestHeaders,
  isAllowed,
  returnedHeaders,
  sameOrigin
} from '@/lib/proxyRules';
import { SESSION_COOKIE } from '@/lib/session-cookie';

// The browser's way to the platform's API. It forwards only the endpoints listed in proxyRules, adds
// the signed-in user's token from the httpOnly cookie, and passes the answer back. The token never
// reaches the page, and the backend needs no CORS because the page talks to one origin.

function failure(status, code, message) {
  return NextResponse.json(
    { success: false, error: { code, message } },
    { status, headers: { 'Cache-Control': 'no-store' } }
  );
}

async function forward(request, { params }) {
  const { path } = await params;
  const relative = cleanPath(path);
  const method = request.method;
  if (relative === null || !isAllowed(method, relative)) {
    return failure(404, 'not_found', 'No such endpoint.');
  }
  if (method !== 'GET' && !sameOrigin(request.headers, request.url)) {
    return failure(403, 'bad_origin', 'Requests from other sites are refused.');
  }

  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) {
    return failure(401, 'not_authenticated', 'Authentication required.');
  }

  let body;
  if (method !== 'GET') {
    const declared = Number(request.headers.get('content-length') || 0);
    if (declared > MAX_BODY_BYTES) {
      return failure(413, 'too_large', 'The request is too large.');
    }
    body = await request.text();
    if (body.length > MAX_BODY_BYTES) {
      return failure(413, 'too_large', 'The request is too large.');
    }
  }

  let upstream;
  try {
    upstream = await backendFetch(`/api/v1/${relative}${new URL(request.url).search}`, {
      method,
      headers: forwardedRequestHeaders(request.headers),
      body: body || undefined,
      token,
      signal: request.signal
    });
  } catch {
    return failure(502, 'backend_unavailable', 'The platform is not reachable right now.');
  }

  const response = new NextResponse(upstream.body, {
    status: upstream.status,
    headers: returnedHeaders(upstream.headers)
  });
  if (upstream.status === 401) {
    // The backend no longer accepts this token: do not keep sending it.
    response.cookies.delete(SESSION_COOKIE);
  }
  return response;
}

export async function GET(request, context) {
  return forward(request, context);
}

export async function POST(request, context) {
  return forward(request, context);
}

export async function PUT(request, context) {
  return forward(request, context);
}

export async function PATCH(request, context) {
  return forward(request, context);
}

export async function DELETE(request, context) {
  return forward(request, context);
}
