import { brand } from '../../config/brand';

// Neutral shield-and-check glyph plus the product name. Replace with the real logo when there is one.
export default function BrandMark({ showName = true }) {
  return (
    <span className="brand-mark">
      <svg
        className="brand-mark-glyph"
        viewBox="0 0 32 32"
        width="28"
        height="28"
        aria-hidden="true"
        focusable="false"
      >
        <path
          d="M16 3 4 8v8c0 7 5 11.5 12 13 7-1.5 12-6 12-13V8L16 3Z"
          fill="currentColor"
          opacity="0.14"
        />
        <path
          d="M16 3 4 8v8c0 7 5 11.5 12 13 7-1.5 12-6 12-13V8L16 3Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinejoin="round"
        />
        <path
          d="m11 16.5 3.6 3.6L21.5 13"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      {showName ? <span className="brand-mark-name">{brand.name}</span> : <span className="sr-only">{brand.name}</span>}
    </span>
  );
}
