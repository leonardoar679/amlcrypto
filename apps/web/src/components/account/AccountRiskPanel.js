'use client';

import { useState } from 'react';
import { IncomingIcon, OutgoingIcon, HelpCircleIcon } from '../icons/Icons';

const PAIRS = [
  [0, 0],
  [1, 2],
  [2, 1]
];

function FlowDiagram({ incoming, outgoing }) {
  const leftY = [40, 108, 176];
  const rightY = [40, 108, 176];
  const leftX = 14;
  const rightX = 386;
  const midX = 200;

  return (
    <svg viewBox="0 0 400 216" className="account-flow-svg">
      {PAIRS.map(([sourceIdx, targetIdx], i) => {
        const y1 = leftY[sourceIdx];
        const y2 = rightY[targetIdx];
        const weight = (incoming[sourceIdx] + outgoing[targetIdx]) / 2;
        const strokeWidth = Math.max(2, (weight / 100) * 30);
        const isAccent = i === 2;
        return (
          <path
            key={i}
            d={`M${leftX},${y1} C${midX - 40},${y1} ${midX - 40},${y2} ${rightX},${y2}`}
            fill="none"
            stroke={isAccent ? '#8b7fd6' : 'var(--border)'}
            strokeOpacity={isAccent ? 0.85 : 1}
            strokeWidth={strokeWidth}
          />
        );
      })}
      <rect x={midX - 3} y="24" width="6" height="168" rx="3" fill="var(--text-soft)" opacity="0.4" />

      {incoming.map((value, i) => (
        <text key={`in-${i}`} x={leftX} y={leftY[i] - 12} className="account-flow-label">
          {value}%
        </text>
      ))}
      {outgoing.map((value, i) => (
        <text key={`out-${i}`} x={rightX} y={rightY[i] - 12} textAnchor="end" className="account-flow-label">
          {value}%
        </text>
      ))}
    </svg>
  );
}

function riskZone(score) {
  if (score < 40) return 'low';
  if (score < 70) return 'medium';
  return 'high';
}

function RiskRadar({ values }) {
  const cx = 100;
  const cy = 104;
  const r = 78;
  const angles = [-90, 30, 150];

  const pointAt = (angle, radius) => {
    const rad = (angle * Math.PI) / 180;
    return [cx + radius * Math.cos(rad), cy + radius * Math.sin(rad)];
  };

  const gridPolygon = (radius) => angles.map((angle) => pointAt(angle, radius).join(',')).join(' ');
  const dataPoints = angles.map((angle, i) => pointAt(angle, r * values[i]));
  const dataPolygon = dataPoints.map((point) => point.join(',')).join(' ');

  return (
    <svg viewBox="0 0 200 208" className="account-radar-svg">
      <polygon points={gridPolygon(r)} className="account-radar-grid" />
      <polygon points={gridPolygon(r * 0.66)} className="account-radar-grid" />
      <polygon points={gridPolygon(r * 0.33)} className="account-radar-grid" />
      {angles.map((angle, i) => {
        const [x, y] = pointAt(angle, r);
        return <line key={i} x1={cx} y1={cy} x2={x} y2={y} className="account-radar-axis" />;
      })}
      <polygon points={dataPolygon} className="account-radar-data" />
      {dataPoints.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r="3.2" className="account-radar-dot" />
      ))}
    </svg>
  );
}

export default function AccountRiskPanel({ incoming, outgoing, riskScore, flows, radarValues }) {
  const [activeTab, setActiveTab] = useState('transactions');
  const zone = riskZone(riskScore);
  const activeFlow = flows[activeTab];

  return (
    <div className="account-risk-panel">
      <div className="account-risk-flow card">
        <div className="account-flow-summary">
          <div className="account-flow-stat">
            <IncomingIcon width={18} height={18} />
            <span>Incoming</span>
            <strong>${incoming}</strong>
          </div>
          <div className="account-flow-stat">
            <OutgoingIcon width={18} height={18} />
            <span>Outgoing</span>
            <strong>${outgoing}</strong>
          </div>
        </div>

        <FlowDiagram incoming={activeFlow.incoming} outgoing={activeFlow.outgoing} />

        <div className="account-flow-tabs">
          <button
            type="button"
            className={`account-flow-tab${activeTab === 'transactions' ? ' is-active' : ''}`}
            onClick={() => setActiveTab('transactions')}
          >
            Transactions
          </button>
          <button
            type="button"
            className={`account-flow-tab${activeTab === 'rating' ? ' is-active' : ''}`}
            onClick={() => setActiveTab('rating')}
          >
            Social rating
          </button>
        </div>
      </div>

      <div className="account-risk-score card">
        <div className="account-risk-score-head">
          <span>Final risk score</span>
          <HelpCircleIcon width={15} height={15} />
        </div>
        <div className={`account-risk-score-value account-risk-score-${zone}`}>{riskScore}</div>

        <div className="account-risk-factors-head">
          <span>Risk score factors</span>
          <HelpCircleIcon width={15} height={15} />
        </div>

        <div className="account-radar-wrap">
          <span className="account-radar-tag">Counterparty interaction</span>
          <RiskRadar values={radarValues} />
        </div>
      </div>
    </div>
  );
}
