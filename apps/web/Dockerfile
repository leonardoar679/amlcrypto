# Built and smoke-tested by the docker-build job in CI (.github/workflows/ci.yml). It has never been
# built on the development machine, which has no Docker; what the image runs (the standalone server
# and the files beside it) was run there by hand, the same way, to check it.
#
# Two stages: the first installs the dependencies from the lock file and builds; the second holds only
# the self-contained server the build produces, the files it serves, and no source, no dev tools.
FROM node:22-slim AS build

WORKDIR /srv/web
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# The build fetches the fonts the pages use, so it needs the network.
ENV NEXT_TELEMETRY_DISABLED=1 NEXT_OUTPUT=standalone
RUN npm run build


FROM node:22-slim

WORKDIR /srv/web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0

# The server, then what it serves from disk (the build leaves these out of the server on purpose).
COPY --from=build --chown=node:node /srv/web/.next/standalone ./
COPY --from=build --chown=node:node /srv/web/.next/static ./.next/static
COPY --from=build --chown=node:node /srv/web/public ./public

# Not root: the image has the user "node" already.
USER node
EXPOSE 3000

# BACKEND_URL (where the API is, as this container reaches it) is read when the server starts, not
# when the image is built, so one image serves any deployment.
CMD ["node", "server.js"]
