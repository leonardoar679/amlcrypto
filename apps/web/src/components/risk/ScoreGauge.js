import { LEVEL_LABELS, bandOf, levelClass } from '../../lib/risk';

// The score on a half-circle that is split into the four bands. The numbers on the scale are the
// platform's own cut-offs, so what is drawn is what the level means.
const BANDS = [
  { level: 'LOW', from: 0, to: 20 },
  { level: 'MODERATE', from: 20, to: 40 },
  { level: 'HIGH', from: 40, to: 70 },
  { level: 'CRITICAL', from: 70, to: 100 }
];

const CENTER = 100;
const RADIUS = 80;

function pointAt(score, radius = RADIUS) {
  const angle = Math.PI - (score / 100) * Math.PI;
  return [CENTER + radius * Math.cos(angle), CENTER - radius * Math.sin(angle)];
}

function arc(from, to) {
  const [x1, y1] = pointAt(from);
  const [x2, y2] = pointAt(to);
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${RADIUS} ${RADIUS} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

export default function ScoreGauge({ score, level }) {
  const scored = typeof score === 'number' && !Number.isNaN(score);
  const shown = level || (scored ? bandOf(score) : null);
  const [markerX, markerY] = scored ? pointAt(Math.max(0, Math.min(100, score))) : [CENTER, CENTER];

  return (
    <figure className="gauge">
      <svg viewBox="0 0 200 118" role="img" aria-label={scored ? `Risk score ${score} out of 100, ${LEVEL_LABELS[shown] || ''}` : 'No risk score'}>
        {BANDS.map((band) => (
          <path key={band.level} d={arc(band.from, band.to)} className={`gauge-segment ${levelClass(band.level)}`} />
        ))}
        {scored ? <circle cx={markerX.toFixed(2)} cy={markerY.toFixed(2)} r="8" className="gauge-marker" /> : null}
        <text x={CENTER} y="92" textAnchor="middle" className="gauge-value">
          {scored ? score : '-'}
        </text>
        <text x={CENTER} y="112" textAnchor="middle" className="gauge-unit">
          {scored ? 'out of 100' : 'no score'}
        </text>
      </svg>
      <figcaption className="gauge-scale">
        {BANDS.map((band) => (
          <span key={band.level} className={`gauge-key${band.level === shown ? ' is-current' : ''}`}>
            <i className={`gauge-swatch ${levelClass(band.level)}`} aria-hidden="true" />
            {LEVEL_LABELS[band.level]} {band.from}-{band.to === 100 ? 100 : band.to - 1}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
