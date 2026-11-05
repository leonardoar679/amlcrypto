'use client';

import { useState } from 'react';
import AppShell from '../../src/components/layout/AppShell';
import AppCheckBar from '../../src/components/layout/AppCheckBar';
import PricingTiers, { PRICING_TIERS } from '../../src/components/pricing/PricingTiers';
import PricingCheckout from '../../src/components/pricing/PricingCheckout';
import PaymentHistoryTable from '../../src/components/pricing/PaymentHistoryTable';
import { getStoredQuota, setStoredQuota } from '../../src/lib/demoAuth';

const INITIAL_HISTORY = [
  { invoiceId: 'INV-KIPD37TK', date: '08.06.2026 11:19:40', checks: 10, price: '5 USD', status: 'Complete' },
  { invoiceId: 'c3e046bb-c501-4da7-8181-ec0d1a9118a8', date: '30.05.2026 05:41:26', checks: 3, price: '0 USD', status: 'Complete' }
];

function formatNow() {
  const now = new Date();
  return now.toLocaleString('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric' }).replace(/\//g, '.') +
    ' ' + now.toLocaleTimeString('en-GB');
}

export default function PricingPage() {
  const [selected, setSelected] = useState(0);
  const [unit, setUnit] = useState('fiat');
  const [promoCode, setPromoCode] = useState('');
  const [promoStatus, setPromoStatus] = useState('');
  const [history, setHistory] = useState(INITIAL_HISTORY);

  const handleApplyPromo = () => {
    if (!promoCode.trim()) {
      setPromoStatus('Enter a promocode first.');
      return;
    }
    setPromoStatus('Promocode is not valid or has expired.');
  };

  const handleCheckout = () => {
    const tier = PRICING_TIERS[selected];
    setStoredQuota(getStoredQuota() + tier.checks);
    setHistory((current) => [
      {
        invoiceId: `INV-${Math.random().toString(36).slice(2, 8).toUpperCase()}`,
        date: formatNow(),
        checks: tier.checks,
        price: `${tier.price.replace('$', '')} USD`,
        status: 'Complete'
      },
      ...current
    ]);
  };

  return (
    <AppShell>
      <AppCheckBar />

      <div className="account-page pricing-page">
        <PricingTiers
          selected={selected}
          onSelect={setSelected}
          unit={unit}
          onUnitChange={setUnit}
          promoCode={promoCode}
          onPromoCodeChange={setPromoCode}
          onApplyPromo={handleApplyPromo}
          promoStatus={promoStatus}
        />

        <PricingCheckout onCheckout={handleCheckout} />

        <PaymentHistoryTable items={history} />
      </div>
    </AppShell>
  );
}
