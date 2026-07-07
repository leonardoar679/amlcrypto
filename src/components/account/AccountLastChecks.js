'use client';

import { useState } from 'react';
import { SearchIcon, SortIcon } from '../icons/Icons';

export default function AccountLastChecks({ items, heading = 'My last checks', showAllButton = true }) {
  const [query, setQuery] = useState('');

  return (
    <div className="account-section card">
      <div className="account-section-head">
        <h2>{heading}</h2>
        <div className="account-last-checks-search">
          <input
            type="text"
            placeholder="Enter address"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button type="button">
            <SearchIcon width={14} height={14} />
            Find
          </button>
        </div>
      </div>

      <div className="account-table-scroll">
        <table className="account-table">
          <thead>
            <tr>
              <th>Date <SortIcon width={12} height={12} /></th>
              <th>Address</th>
              <th>Risk level <SortIcon width={12} height={12} /></th>
              <th>PDF</th>
              <th>Report</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={`${item.address}-${item.date}`}>
                <td className="account-table-muted">{item.date}</td>
                <td className="account-table-address">{item.address}</td>
                <td>
                  <span className={`score-pill score-${item.risk.toLowerCase()}`}>{item.percent}%</span>
                  <span className="account-risk-word">{item.risk}</span>
                </td>
                <td>
                  {item.pdf ? <a href={item.pdf} className="account-table-link">Result &gt;&gt;</a> : <span className="account-table-muted">No PDF</span>}
                </td>
                <td>
                  <a href="#view" className="account-table-link">View &gt;&gt;</a>
                </td>
                <td>
                  <button type="button" className="btn-check-again">Check again</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showAllButton ? (
        <div className="account-show-all">
          <button type="button" className="btn-show-all">Show all checks</button>
        </div>
      ) : (
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
      )}
    </div>
  );
}
