import GraphExplorer from '@/components/graph/GraphExplorer';
import { CHAINS } from '@/lib/chains';
import { firstParam } from '@/lib/links';

export const metadata = { title: 'Transaction graph' };

const DIRECTIONS = ['both', 'in', 'out'];

function whole(value, low, high, fallback) {
  const number = Number.parseInt(value, 10);
  return Number.isInteger(number) && number >= low && number <= high ? number : fallback;
}

// The graph is described by its address in the link, so a view can be shared or bookmarked.
export default async function GraphPage({ searchParams }) {
  const params = await searchParams;
  const chain = firstParam(params?.chain);
  const address = firstParam(params?.address);
  const direction = firstParam(params?.direction);

  return (
    <div className="app-page">
      <GraphExplorer
        chain={CHAINS.some((known) => known.slug === chain) ? chain : ''}
        address={typeof address === 'string' ? address.slice(0, 200) : ''}
        depth={whole(firstParam(params?.depth), 1, 3, 1)}
        direction={DIRECTIONS.includes(direction) ? direction : 'both'}
        days={whole(firstParam(params?.days), 1, 36500, 0)}
      />
    </div>
  );
}
