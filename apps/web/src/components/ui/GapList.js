import { parseNodeId, sameAddress } from '../../lib/chains';
import { describeGap, groupGaps } from '../../lib/graph';
import { AddressLink } from './Links';

// What a graph or a report could not cover. The platform reports one gap per address; this shows
// "8 addresses have no stored history" once, with the addresses behind it one click away. The address
// the page is about (`subject`) is not listed among them: the page already says whose it is.
export default function GapList({ gaps, chain, subject }) {
  const groups = groupGaps(gaps || []);
  if (groups.length === 0) {
    return null;
  }
  return (
    <ul className="plain-list">
      {groups.map((group, index) => {
        const others = group.nodes.filter((id) => !sameAddress(chain, parseNodeId(id)?.address, subject));
        return (
          <li key={`${group.code}-${index}`}>
            {describeGap(group)}
            {others.length > 0 ? (
              <details className="small">
                <summary>Which {others.length === 1 ? 'address' : 'addresses'}</summary>
                <ul className="plain-list">
                  {others.map((id) => (
                    <li key={id}>
                      <AddressLink chain={chain} address={parseNodeId(id)?.address || id} explorer={false} />
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
