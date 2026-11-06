import { supportMailto } from '../config/brand';

export const navItems = [
  { label: 'Products', href: '/#products' },
  { label: 'Screening', href: '/products/screening' },
  { label: 'FAQ', href: '/faq' },
  { label: 'Contact', href: supportMailto }
];

export const heroBullets = [
  {
    title: 'know what a counterparty brings with it',
    text: 'direct and indirect exposure to risky addresses'
  },
  {
    title: 'see the evidence behind a score',
    text: 'transactions, data sources and the rule that fired'
  },
  {
    title: 'keep a record',
    text: 'of what was checked, when, and how complete it was'
  }
];

export const supportedChains = ['Ethereum', 'BNB Smart Chain', 'TRON', 'Bitcoin', 'Solana'];

export const whyScreen = [
  'Funds rarely move in a straight line. A payment you receive may have passed through several addresses, a mixer or a bridge before it reached you.',
  'Exchanges and other regulated services look at where incoming funds came from, and may hold or reject a deposit that carries exposure to risky addresses.',
  'Looking at a counterparty before you transact shows what you would be taking on: direct exposure to sanctioned or high-risk addresses, and indirect exposure a few hops away.',
  'A result is only as good as its coverage. Every screening here states how much history was examined and what could not be, because “no findings” is not the same as “safe”.'
];

export const reasons = [
  {
    title: 'Risk is often not at the first hop',
    text: 'Exposure to a risky address can sit two or three transfers away from your counterparty. Screening follows funds beyond the first hop, within stated limits.'
  },
  {
    title: 'Sanctions matches stand on their own',
    text: 'A direct match against an official list is reported as its own finding, not averaged away by unrelated low-risk signals.'
  },
  {
    title: 'Findings come with evidence',
    text: 'Each finding points to the transactions, the data source and the version of the rule that produced it, so a reviewer can check it.'
  },
  {
    title: 'Coverage is always stated',
    text: 'A result says how much history was examined and which parts were unavailable. A missing finding is never presented as proof of safety.'
  },
  {
    title: 'Results are repeatable',
    text: 'The same address and the same data produce the same score, and earlier results are kept so changes over time can be seen.'
  },
  {
    title: 'Built for review',
    text: 'Notes, cases and exportable reports support the people who have to explain a decision later.'
  }
];

export const reportItems = [
  { title: 'Summary', text: 'Risk score, level and confidence, with the coverage of the screening.' },
  { title: 'Findings', text: 'Each rule that fired, its severity and the evidence behind it.' },
  { title: 'Exposure paths', text: 'How funds connect the address to risky counterparties, with hops and amounts.' },
  { title: 'Counterparties', text: 'Incoming and outgoing counterparties and what is known about them.' },
  { title: 'Transactions', text: 'The transactions examined, with hashes and the data source for each.' },
  { title: 'Data provenance', text: 'Providers used, when the data was last refreshed, and rule versions.' }
];

export const footerLinks = {
  products: [
    { label: 'Address screening', href: '/products/screening' },
    { label: 'Transaction graph', href: '/products/graph' },
    { label: 'Risk API', href: '/products/risk-api' },
    { label: 'Investigations', href: '/products/investigations' }
  ],
  resources: [
    { label: 'FAQ', href: '/faq' },
    { label: 'Try a screening', href: '/check' },
    { label: 'Sign in', href: '/auth' }
  ]
};

export const faqs = [
  {
    question: 'What is a risk score?',
    answer:
      'A number from 0 to 100 that summarises the risk findings for an address; higher means more risk. Each score comes with the findings behind it and a separate confidence value, because a weak pattern and an exact match against an official list are not equally reliable.'
  },
  {
    question: 'Is what I see on this site real data?',
    answer:
      'Yes. Once you are signed in, screenings read the transaction history of the address from blockchain data providers and match it against official sanctions listings. The weights and thresholds behind the score are still being calibrated, so read a score as an indicator to review, not as a verdict.'
  },
  {
    question: 'Does a clean result mean an address is safe?',
    answer:
      'No. A result covers only the history and data sources that were examined, and every result states that coverage. Missing findings are never proof of safety.'
  },
  {
    question: 'Which networks are supported?',
    answer:
      'Ethereum, BNB Smart Chain, TRON, Bitcoin and Solana. How much history can be read for an address depends on the data providers behind your deployment, and every result states how complete its data was.'
  },
  {
    question: 'What is the difference between screening an address and a transaction?',
    answer:
      'Address screening looks at the history of an address and its counterparties. Transaction screening looks at the funds in one specific transaction. The two results can differ.'
  },
  {
    question: 'Why can a score change without new transactions?',
    answer:
      'Risk can come from counterparties, and what is known about them changes: lists are updated, labels are added, and activity further along the chain appears. Each result records when it was produced and with which data and rule versions.'
  },
  {
    question: 'Where does address attribution come from?',
    answer:
      'From official sanctions lists and other sources, each recorded with its origin and a confidence level. Official listings, analytics attributions, community reports and behavioural inference are kept distinct and are not treated as equally reliable.'
  },
  {
    question: 'Are the addresses I screen stored?',
    answer:
      'Screenings are stored together with the blockchain data they used so that repeat checks can reuse it. Retention and sharing rules will be published before launch.'
  }
];
