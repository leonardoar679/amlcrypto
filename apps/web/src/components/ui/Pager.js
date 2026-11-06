// Previous / next for a list that comes in pages. `onChange` gets the new offset.
export default function Pager({ total, limit, offset, onChange }) {
  if (!total || total <= limit) {
    return null;
  }
  const first = offset + 1;
  const last = Math.min(offset + limit, total);
  return (
    <div className="pager">
      <span>
        {first}-{last} of {total}
      </span>
      <div className="pager-buttons">
        <button
          className="btn btn-secondary btn-small"
          type="button"
          disabled={offset <= 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
          Previous
        </button>
        <button
          className="btn btn-secondary btn-small"
          type="button"
          disabled={offset + limit >= total}
          onClick={() => onChange(offset + limit)}
        >
          Next
        </button>
      </div>
    </div>
  );
}
