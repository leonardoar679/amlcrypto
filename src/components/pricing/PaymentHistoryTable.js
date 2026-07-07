'use client';

import { SortIcon } from '../icons/Icons';

export default function PaymentHistoryTable({ items }) {
  return (
    <div className="account-section card">
      <div className="account-section-head">
        <h2>Payment history</h2>
      </div>

      <div className="account-table-scroll">
        <table className="account-table">
          <thead>
            <tr>
              <th>Invoice ID <SortIcon width={12} height={12} /></th>
              <th>Order date <SortIcon width={12} height={12} /></th>
              <th>Checks count <SortIcon width={12} height={12} /></th>
              <th>Price <SortIcon width={12} height={12} /></th>
              <th>Status <SortIcon width={12} height={12} /></th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.invoiceId}>
                <td className="account-table-address">{item.invoiceId}</td>
                <td className="account-table-muted">{item.date}</td>
                <td>{item.checks}</td>
                <td>{item.price}</td>
                <td>
                  <span className="pricing-status-badge">{item.status}</span>
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
