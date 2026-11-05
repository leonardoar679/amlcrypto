// Stands in for artwork that has not been made yet. Keeps the layout honest without borrowing images.
export default function ImagePlaceholder({ label, ratio = '16 / 10', className = '' }) {
  return (
    <div
      className={`image-placeholder${className ? ` ${className}` : ''}`}
      style={{ aspectRatio: ratio }}
      role="img"
      aria-label={label}
    >
      <span>{label}</span>
    </div>
  );
}
