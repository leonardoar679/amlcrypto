'use client';

import { UsersGroupIcon, IncomingIcon, OutgoingIcon, SortIcon, SearchIcon } from '../icons/Icons';

export default function AccountCounterparties({ items, incomingTotal, outgoingTotal }) {
  return (
    <div className="account-section card">
      <div className="account-section-head">
        <h2>
          <UsersGroupIcon width={18} height={18} />
          Counterparties
        </h2>
        <div className="account-stat-badges">
          <span className="account-stat-badge">
            <UsersGroupIcon width={14} height={14} />
            Unique
            <strong>{items.length}</strong>
          </span>
          <span className="account-stat-badge">
            <IncomingIcon width={14} height={14} />
            Incoming
            <strong>{incomingTotal.count}</strong>
            ${incomingTotal.amount}
          </span>
          <span className="account-stat-badge">
            <OutgoingIcon width={14} height={14} />
            Ougoing
            <strong>{outgoingTotal.count}</strong>
            ${outgoingTotal.amount}
          </span>
        </div>
      </div>

      <div className="account-table-scroll">
        <table className="account-table">
          <thead>
            <tr>
              <th>Address <SortIcon width={12} height={12} /></th>
              <th>Incoming <SortIcon width={12} height={12} /></th>
              <th>Ougoing <SortIcon width={12} height={12} /></th>
              <th>Check date <SortIcon width={12} height={12} /></th>
              <th>Risk level <SortIcon width={12} height={12} /></th>
              <th>PDF</th>
              <th>Report</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.address}>
                <td className="account-table-address">{item.address}</td>
                <td>{item.incoming ? `$${item.incoming}` : '$0'}</td>
                <td>{item.outgoing ? `$${item.outgoing}` : '$0'}</td>
                <td className="account-table-muted">N/A</td>
                <td className="account-table-muted">N/A</td>
                <td className="account-table-muted">N/A</td>
                <td>
                  <button type="button" className="btn-check-address">
                    <SearchIcon width={13} height={13} />
                    Check address
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="account-table-footer">
        <span>{items.length} items</span>
        <div className="account-pagination">
          <button type="button" disabled>‹</button>
          <span className="account-pagination-page">1</span>
          <button type="button" disabled>›</button>
        </div>
        <span>
          Show <strong>10</strong> records
        </span>
      </div>
    </div>
  );
}
