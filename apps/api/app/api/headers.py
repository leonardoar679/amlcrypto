"""Headers every response carries, whoever made it."""

from __future__ import annotations

# An API answer is data about a person's work: it is never kept by a cache or a proxy, never read as
# anything but what its content type says, and never leaks the address of the page that asked.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}
