'use client';

import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { en } from '@/i18n/en';
import { zhTw } from '@/i18n/zh_tw';
import { zhCn } from '@/i18n/zh_cn';
import { ko } from '@/i18n/ko';
import { ja } from '@/i18n/ja';

export type Language = 'en' | 'zh-TW' | 'zh-CN' | 'ko' | 'ja';
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Dictionary = any;

// Info: (20260120 - Luphia) Helper to get nested value by key string "auth_modal.login_btn"
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function getNestedValue(obj: any, path: string): string {
  return path.split('.').reduce((prev, curr) => (prev ? prev[curr] : null), obj) || path;
}

interface II18nContextType {
  language: Language;
  setLanguage: (lang: Language) => void;
  t: (key: string, options?: Record<string, string | number>) => string;
}

const I18nContext = createContext<II18nContextType | undefined>(undefined);

const dictionaries: Record<Language, Dictionary> = {
  en,
  'zh-TW': zhTw,
  'zh-CN': zhCn,
  ko,
  ja,
};

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>('zh-TW'); // Default to zh-TW
  const [dictionary, setDictionary] = useState<Dictionary>(zhTw);

  useEffect(() => {
    // Info: (20260120 - Luphia) Load persisted language
    const savedLang = localStorage.getItem('isunfa_lang') as Language;
    if (savedLang && Object.keys(dictionaries).includes(savedLang) && savedLang !== language) {
      setLanguage(savedLang);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setLanguage = (lang: Language) => {
    setLanguageState(lang);
    setDictionary(dictionaries[lang]);
    localStorage.setItem('isunfa_lang', lang);
  };

  const t = (key: string, options?: Record<string, string | number>): string => {
    let text = getNestedValue(dictionary, key);
    if (options) {
      Object.entries(options).forEach(([k, v]) => {
        text = text.replace(new RegExp(`{{${k}}}`, 'g'), String(v));
      });
    }
    return text;
  };

  return (
    <I18nContext.Provider value={{ language, setLanguage, t }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useTranslation() {
  const context = useContext(I18nContext);
  if (context === undefined) {
    throw new Error('useTranslation must be used within an I18nProvider');
  }
  return context;
}
