import { STORAGE_KEYS } from '../../lib/storageKeys';

// Sets the saved colour theme on <html> while the page is still being parsed, before anything is
// painted, so a person who chose dark never sees a light flash. With no saved choice the browser's
// own preference is used. (It runs on a full page load; after that the page is not reloaded.)
const script = `try{var t=localStorage.getItem(${JSON.stringify(STORAGE_KEYS.theme)});if(t!=='light'&&t!=='dark'){t=window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}document.documentElement.dataset.theme=t}catch(e){}`;

export default function ThemeScript() {
  return <script dangerouslySetInnerHTML={{ __html: script }} />;
}
