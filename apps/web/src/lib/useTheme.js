'use client';

import { useCallback, useEffect, useState } from 'react';
import { STORAGE_KEYS, writeStored } from './storageKeys';

// The saved theme is applied to the page before it paints by the script in ThemeScript. This hook
// only reads what the page already shows, and writes the preference when the person changes it (not
// before: a first visit must not overwrite a saved choice).
export function useTheme() {
  const [theme, setTheme] = useState('light');

  useEffect(() => {
    setTheme(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');
  }, []);

  const toggleTheme = useCallback(() => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    writeStored(STORAGE_KEYS.theme, next);
    setTheme(next);
  }, []);

  return [theme, toggleTheme];
}
