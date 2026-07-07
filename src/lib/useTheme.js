'use client';

import { useEffect, useState } from 'react';

export function useTheme() {
  const [theme, setTheme] = useState('light');

  useEffect(() => {
    const storedTheme = window.localStorage.getItem('aml-theme');
    if (storedTheme === 'light' || storedTheme === 'dark') {
      setTheme(storedTheme);
    }
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem('aml-theme', theme);
  }, [theme]);

  const toggleTheme = () => setTheme((current) => (current === 'dark' ? 'light' : 'dark'));

  return [theme, toggleTheme];
}
