import Link from 'next/link';
import { brand, supportMailto } from '../../config/brand';
import { footerLinks } from '../../data/siteContent';

export default function Footer() {
  return (
    <footer className="site-footer">
      <div className="container footer-main">
        <div className="footer-col">
          <h4>Products</h4>
          <ul>
            {footerLinks.products.map((link) => (
              <li key={link.label}>
                <Link href={link.href}>{link.label}</Link>
              </li>
            ))}
          </ul>
        </div>

        <div className="footer-col">
          <h4>Resources</h4>
          <ul>
            {footerLinks.resources.map((link) => (
              <li key={link.label}>
                <Link href={link.href}>{link.label}</Link>
              </li>
            ))}
          </ul>
        </div>

        <div className="footer-col">
          <h4>Contact</h4>
          <a className="footer-email" href={supportMailto}>
            {brand.supportEmail}
          </a>
          <a className="footer-incident" href={supportMailto}>
            Report a problem
          </a>
        </div>
      </div>
      <div className="container footer-note">
        <p>
          Screening results are risk indicators computed from the data that could be read, not legal or
          compliance advice. Finding nothing is not a clearance.
        </p>
      </div>
    </footer>
  );
}
