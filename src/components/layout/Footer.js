import Link from 'next/link';
import { footerLinks, socialLinks } from '../../data/siteContent';
import { MailIcon, TelegramIcon, WhatsappIcon, VkIcon } from '../icons/Icons';

const socialIcons = {
  mail: MailIcon,
  telegram: TelegramIcon,
  whatsapp: WhatsappIcon,
  vk: VkIcon
};

export default function Footer() {
  return (
    <footer className="site-footer">
      <div className="container footer-main">
        <div className="footer-col">
          <h4>Our products</h4>
          <ul>
            {footerLinks.products.map((link) => (
              <li key={link.label}>
                <Link href={link.href}>{link.label}</Link>
              </li>
            ))}
          </ul>
        </div>

        <div className="footer-col">
          <h4>Blog</h4>
          <ul>
            {footerLinks.blog.map((link) => (
              <li key={link.label}>
                <Link href={link.href}>{link.label}</Link>
              </li>
            ))}
          </ul>
        </div>

        <div className="footer-col">
          <h4>About</h4>
          <ul>
            {footerLinks.about.map((link) => (
              <li key={link.label}>
                <Link href={link.href}>{link.label}</Link>
              </li>
            ))}
          </ul>
        </div>

        <div className="footer-col">
          <h4>Contact center</h4>
          <div className="footer-social">
            {socialLinks.map((social) => {
              const Icon = socialIcons[social.icon];
              return (
                <a
                  key={social.label}
                  className="social-icon"
                  href={social.href}
                  aria-label={social.label}
                  target="_blank"
                  rel="noreferrer"
                >
                  <Icon />
                </a>
              );
            })}
          </div>
          <a className="footer-email" href="mailto:info@amlcrypto.io">
            info@amlcrypto.io
          </a>
          <a className="footer-incident" href="mailto:info@amlcrypto.io">
            Report an incident
          </a>
        </div>
      </div>
    </footer>
  );
}
