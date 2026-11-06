'use client';

import { useState } from 'react';
import { signOut } from '../../lib/api';
import { chainName } from '../../lib/chains';
import { ago, factorLabel, formatWhen } from '../../lib/risk';
import { useApi } from '../../lib/useApi';
import { SeverityChip } from '../risk/Chips';
import { safeHref } from '../../lib/links';
import { ErrorNotice, Loading } from '../ui/Notice';

function Section({ title, state, children, intro }) {
  return (
    <section className="card">
      <h2>{title}</h2>
      {intro ? <p className="muted">{intro}</p> : null}
      {state.loading ? <Loading label={`Loading ${title.toLowerCase()}`} /> : null}
      {state.error && !state.data ? <ErrorNotice error={state.error} onRetry={state.reload} /> : null}
      {state.data ? children(state.data) : null}
    </section>
  );
}

function Account() {
  const me = useApi('auth/me');
  const [leaving, setLeaving] = useState(false);

  const logout = async () => {
    setLeaving(true);
    try {
      await signOut();
    } finally {
      window.location.assign('/');
    }
  };

  return (
    <Section title="Account" state={me}>
      {({ user, organization }) => (
        <>
          <dl className="facts facts-grid">
            <div>
              <dt>Email</dt>
              <dd>{user.email}</dd>
            </div>
            <div>
              <dt>Organization</dt>
              <dd>{organization.name}</dd>
            </div>
            <div>
              <dt>Last signed in</dt>
              <dd>{user.last_login_at ? formatWhen(user.last_login_at) : '-'}</dd>
            </div>
          </dl>
          <p className="muted">
            Accounts and passwords are managed by your organization&apos;s administrator. Everything you screen belongs to the
            organization: colleagues see it, other organizations do not.
          </p>
          <button className="btn btn-secondary" type="button" onClick={logout} disabled={leaving}>
            Sign out
          </button>
        </>
      )}
    </Section>
  );
}

function Providers() {
  const providers = useApi('providers');
  return (
    <Section
      title="Data providers"
      state={providers}
      intro="Where blockchain data comes from. Access keys are held by the server and are never shown here."
    >
      {(list) => (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Provider</th>
                <th scope="col">Status</th>
                <th scope="col">Chains</th>
                <th scope="col">Last 24 hours</th>
              </tr>
            </thead>
            <tbody>
              {list.map((provider) => (
                <tr key={provider.name}>
                  <td>
                    <strong>{provider.display_name}</strong>
                    <details className="small">
                      <summary>Notes</summary>
                      <p className="muted">{provider.notes}</p>
                    </details>
                  </td>
                  <td>
                    {provider.configured ? (
                      <span className="chip chip-ok">Configured</span>
                    ) : (
                      <span className="chip chip-warn">{provider.needs_key ? 'Needs a key' : 'Not configured'}</span>
                    )}
                    {provider.circuit !== 'closed' ? <span className="chip chip-bad">circuit {provider.circuit}</span> : null}
                  </td>
                  <td>
                    {provider.chains.map((slug) => chainName(slug)).join(', ')}
                    {provider.paid_chains.length ? (
                      <div className="muted small">paid plan: {provider.paid_chains.map((slug) => chainName(slug)).join(', ')}</div>
                    ) : null}
                  </td>
                  <td>
                    {provider.usage_24h.requests} requests
                    {provider.usage_24h.errors || provider.usage_24h.rate_limited ? (
                      <div className="muted small">
                        {provider.usage_24h.errors} errors, {provider.usage_24h.rate_limited} rate-limited
                      </div>
                    ) : null}
                    {provider.last_error ? <div className="muted small clamp">last error: {provider.last_error}</div> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

function LabelSources() {
  const sources = useApi('intel/sources');
  return (
    <Section
      title="Label sources"
      state={sources}
      intro="What addresses are matched against. A source that has not been refreshed recently is marked stale, and every screening says so."
    >
      {(list) => (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Source</th>
                <th scope="col">Kind</th>
                <th scope="col">Last confirmed</th>
                <th scope="col">Freshness</th>
              </tr>
            </thead>
            <tbody>
              {list.map((source) => {
                const href = safeHref(source.url);
                return (
                  <tr key={source.slug}>
                    <td>
                      {href ? (
                        <a href={href} target="_blank" rel="noopener noreferrer">
                          {source.name}
                          <span className="sr-only"> (opens another site)</span>
                        </a>
                      ) : (
                        source.name
                      )}
                      {source.last_error ? <div className="muted small clamp">last error: {source.last_error}</div> : null}
                    </td>
                    <td>{source.kind}</td>
                    <td title={formatWhen(source.last_checked_at)}>{source.last_checked_at ? ago(source.last_checked_at) : 'never'}</td>
                    <td>
                      <span className={`chip ${source.freshness === 'fresh' ? 'chip-ok' : 'chip-warn'}`}>{source.freshness}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

function Rules() {
  const rules = useApi('risk/rules');
  return (
    <Section
      title="Risk rules"
      state={rules}
      intro="The rules every screening runs. Their weights and thresholds are starting values that have not been calibrated against real cases yet, so treat scores as indicators. Settings are changed by the platform's administrator; each report records the settings it used."
    >
      {({ rules: list }) => (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Rule</th>
                <th scope="col">What it looks for</th>
                <th scope="col">Severity</th>
                <th scope="col">State</th>
              </tr>
            </thead>
            <tbody>
              {list.map((rule) => (
                <tr key={rule.rule_id}>
                  <td>
                    <span className="mono">{rule.rule_id}</span>
                    <div className="muted small">version {rule.version}</div>
                  </td>
                  <td>
                    <strong>{rule.rule_name}</strong>
                    <div className="muted small">{rule.description}</div>
                    <div className="muted small">
                      Factor: {factorLabel(rule.factor)} · chains:{' '}
                      {rule.supported_chains.includes('*') ? 'all' : rule.supported_chains.map((slug) => chainName(slug)).join(', ')}
                    </div>
                    {Object.keys(rule.config).length > 0 ? (
                      <details className="small">
                        <summary>Settings{rule.overridden ? ' (changed from the defaults)' : ''}</summary>
                        <dl className="facts facts-inline">
                          {Object.entries(rule.config).map(([name, value]) => (
                            <div key={name}>
                              <dt>{name}</dt>
                              <dd>
                                {String(value)}
                                {String(rule.defaults[name]) !== String(value) ? (
                                  <span className="muted"> (default {String(rule.defaults[name])})</span>
                                ) : null}
                              </dd>
                            </div>
                          ))}
                        </dl>
                      </details>
                    ) : null}
                  </td>
                  <td>
                    <SeverityChip severity={rule.severity} />
                  </td>
                  <td>{rule.enabled ? <span className="chip chip-ok">On</span> : <span className="chip chip-neutral">Off</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

export default function SettingsView() {
  return (
    <div className="app-page">
      <h1>Settings</h1>
      <Account />
      <Providers />
      <LabelSources />
      <Rules />
    </div>
  );
}
