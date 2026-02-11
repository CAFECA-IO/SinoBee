'use client';

import { useState, useEffect } from 'react';
import { useTranslation } from '@/i18n/i18n_context';
import { Settings, X, Check } from 'lucide-react';
import { MarkdownContent } from '@/components/common/markdown_content';

interface ICookiePreferences {
  necessary: boolean;
  security: boolean;
  analytics: boolean;
}

interface ICookieConsentProps {
  privacyPolicyContent?: string;
}

function Toggle({ checked, onChange, disabled = false }: { checked: boolean; onChange?: (checked: boolean) => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => !disabled && onChange?.(!checked)}
      className={`relative inline-flex h-7 w-12 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-orange-500 focus:ring-offset-2 ${checked
        ? 'bg-green-200 dark:bg-green-200'
        : 'bg-gray-700 dark:bg-gray-700'
        } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
    >
      <span
        aria-hidden="true"
        className={`pointer-events-none inline-block h-6 w-6 transform rounded-full shadow ring-0 transition duration-200 ease-in-out flex items-center justify-center ${checked
          ? 'translate-x-[20px] bg-green-900'
          : 'translate-x-0 bg-gray-400'
          }`}
      >
        {checked && <Check className="mt-1 m-auto h-3.5 w-3.5 text-white" strokeWidth={3} />}
      </span>
    </button>
  );
}

export default function CookieConsent({ privacyPolicyContent = '' }: ICookieConsentProps) {
  const { t } = useTranslation();
  const [showConsent, setShowConsent] = useState(false);
  const [showPreferences, setShowPreferences] = useState(false);
  const [showPrivacy, setShowPrivacy] = useState(false);
  const [preferences, setPreferences] = useState<ICookiePreferences>({
    necessary: true,
    security: true,
    analytics: true,
  });

  useEffect(() => {
    const timer = setTimeout(() => {
      const consent = localStorage.getItem('cookie_consent');
      if (!consent) {
        setShowConsent(true);
      } else {
        try {
          const parsed = JSON.parse(consent);
          if (parsed && typeof parsed === 'object') {
            // Info: (20260104 - Luphia) We could restore perfs here if we wanted to allow editing
            setPreferences(parsed);
          }
        } catch {
          // Info: (20260104 - Luphia) ignore
        }
      }
    }, 0);
    return () => clearTimeout(timer);

    const handleOpenSettings = () => {
      setShowPreferences(true);
    };

    window.addEventListener('openCookieSettings', handleOpenSettings);
    return () => {
      window.removeEventListener('openCookieSettings', handleOpenSettings);
    };
  }, []);

  const saveConsent = (prefs: ICookiePreferences) => {
    localStorage.setItem('cookie_consent', JSON.stringify(prefs));
    setShowConsent(false);
    setShowPreferences(false);
  };

  const handleAcceptAll = () => {
    saveConsent({ necessary: true, security: true, analytics: true });
  };

  const handleRejectAll = () => {
    saveConsent({ necessary: true, security: false, analytics: false });
  };

  const handleSavePreferences = () => {
    saveConsent(preferences);
  };

  const openPrivacy = (e: React.MouseEvent) => {
    e.preventDefault();
    setShowPrivacy(true);
  };

  if (!showConsent) {
    return null;
  }

  // Info: (20260104 - Luphia) Privacy Policy Modal
  if (showPrivacy) {
    return (
      <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
        <div className="flex h-[85vh] w-full max-w-4xl flex-col rounded-xl bg-white shadow-2xl dark:bg-gray-900">
          <div className="flex items-center justify-between border-b border-gray-200 px-6 py-4 dark:border-gray-800">
            <h3 className="text-xl font-bold text-gray-900 dark:text-white">{t('cookie_consent.privacy')}</h3>
            <button onClick={() => setShowPrivacy(false)} className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200">
              <X className="h-6 w-6" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-8 py-6">
            <MarkdownContent content={privacyPolicyContent} theme="dark" />
          </div>
          <div className="border-t border-gray-200 px-6 py-4 dark:border-gray-800">
            <button
              onClick={() => setShowPrivacy(false)}
              className="w-full rounded-md bg-orange-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-orange-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-orange-600"
            >
              {t('common.close')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Info: (20260104 - Luphia) Preferences Modal
  if (showPreferences) {
    return (
      <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
        <div className="w-full max-w-lg rounded-xl bg-white p-6 shadow-2xl dark:bg-gray-900">
          <div className="mb-6 flex items-center justify-between">
            <h3 className="text-xl font-bold text-gray-900 dark:text-white">{t('cookie_consent.customize')}</h3>
            <button onClick={() => setShowPreferences(false)} className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200">
              <X className="h-6 w-6" />
            </button>
          </div>

          <div className="space-y-4">
            <div className="flex items-center justify-between rounded-lg bg-gray-50 p-4 dark:bg-gray-800">
              <span className="font-medium text-gray-900 dark:text-white">{t('cookie_consent.necessary')}</span>
              <Toggle checked={true} disabled={true} />
            </div>

            <div className="flex items-center justify-between rounded-lg bg-gray-50 p-4 dark:bg-gray-800">
              <span className="font-medium text-gray-900 dark:text-white flex-grow">
                {t('cookie_consent.security')}
              </span>
              <Toggle
                checked={preferences.security}
                onChange={(checked) => setPreferences({ ...preferences, security: checked })}
              />
            </div>

            <div className="flex items-center justify-between rounded-lg bg-gray-50 p-4 dark:bg-gray-800">
              <span className="font-medium text-gray-900 dark:text-white flex-grow">
                {t('cookie_consent.analytics')}
              </span>
              <Toggle
                checked={preferences.analytics}
                onChange={(checked) => setPreferences({ ...preferences, analytics: checked })}
              />
            </div>
          </div>

          <div className="mt-8 flex gap-3">
            <button
              onClick={handleSavePreferences}
              className="flex-1 rounded-md bg-orange-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-orange-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-orange-600"
            >
              {t('cookie_consent.save')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Info: (20260104 - Luphia) Main Banner
  return (
    <div className="fixed bottom-0 left-0 right-0 z-[100] flex flex-col items-center justify-between gap-4 bg-white p-6 shadow-[0_-4px_6px_-1px_rgba(0,0,0,0.1)] ring-1 ring-gray-900/5 md:flex-row md:px-8 dark:bg-gray-900 dark:ring-white/10">
      <div className="text-sm md:text-base text-gray-600 dark:text-gray-300">
        <p>
          {t('cookie_consent.message')}{' '}
          <button type="button" onClick={openPrivacy} className="font-semibold text-orange-600 hover:text-orange-500 underline dark:text-orange-400">
            {t('cookie_consent.privacy')}
          </button>
          .
        </p>
      </div>
      <div className="flex flex-wrap gap-3">
        <button
          onClick={() => setShowPreferences(true)}
          className="flex items-center gap-2 rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-orange-500 focus:ring-offset-2 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
        >
          <Settings className="h-4 w-4" />
          {t('cookie_consent.customize')}
        </button>
        <button
          onClick={handleRejectAll}
          className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-orange-500 focus:ring-offset-2 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
        >
          {t('cookie_consent.reject')}
        </button>
        <button
          onClick={handleAcceptAll}
          className="rounded-md bg-orange-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-orange-500 focus:outline-none focus:ring-2 focus:ring-orange-500 focus:ring-offset-2"
        >
          {t('cookie_consent.accept')}
        </button>
      </div>
    </div>
  );
}
