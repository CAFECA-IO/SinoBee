'use client';

import { useState, useEffect } from 'react';
import { Dialog, DialogPanel, DialogTitle, Transition, TransitionChild } from '@headlessui/react';
import { X, Smile } from 'lucide-react';
import { MarkdownContent } from '@/components/common/markdown_content';
import { request } from '@/lib/utils/request';
import { useTranslation } from '@/i18n/i18n_context';

interface ILegalModalProps {
  isOpen: boolean;
  onClose: () => void;
  documentType: 'terms_of_service' | 'privacy_policy' | 'refund_policy' | null;
}

// Info: (20260118 - Luphia) Refactored for scroll detection and interactive buttons
export default function LegalModal({ isOpen, onClose, documentType }: ILegalModalProps) {
  const { t } = useTranslation();
  const [content, setContent] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [hasScrolledToBottom, setHasScrolledToBottom] = useState(false);

  useEffect(() => {
    if (isOpen && documentType) {
      setHasScrolledToBottom(false);
      const fetchContent = async () => {
        setLoading(true);
        try {
          const res = await request<{ payload: { content: string } }>(`/api/v1/documents/${documentType}`);
          setContent(res.payload.content);
        } catch (error) {
          console.error('Failed to fetch legal document:', error);
          setContent('Failed to load document.');
        } finally {
          setLoading(false);
        }
      };
      fetchContent();
    }
  }, [isOpen, documentType]);

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const { scrollTop, scrollHeight, clientHeight } = e.currentTarget;
    // Info: (20260118 - Luphia) Allow small buffer (e.g. 5px)
    if (scrollHeight - scrollTop - clientHeight < 5) {
      setHasScrolledToBottom(true);
    }
  };

  const titleKey = documentType === 'terms_of_service'
    ? 'auth_modal.tos_link'
    : documentType === 'privacy_policy'
      ? 'auth_modal.privacy_link'
      : 'footer.refund';

  return (
    <Transition show={isOpen}>
      <Dialog className="relative z-[60]" onClose={onClose}>
        <TransitionChild
          enter="ease-out duration-300"
          enterFrom="opacity-0"
          enterTo="opacity-100"
          leave="ease-in duration-200"
          leaveFrom="opacity-100"
          leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-gray-500/75 transition-opacity" />
        </TransitionChild>

        <div className="fixed inset-0 z-10 w-screen overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4 text-center sm:p-0">
            <TransitionChild
              enter="ease-out duration-300"
              enterFrom="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
              enterTo="opacity-100 translate-y-0 sm:scale-100"
              leave="ease-in duration-200"
              leaveFrom="opacity-100 translate-y-0 sm:scale-100"
              leaveTo="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
            >
              <DialogPanel className="relative transform overflow-hidden rounded-lg bg-white px-4 pb-4 pt-5 text-left shadow-xl transition-all sm:my-8 sm:w-full sm:max-w-2xl sm:p-6">
                <div className="absolute right-0 top-0 hidden pr-4 pt-4 sm:block">
                  <button
                    type="button"
                    className="rounded-md bg-white text-gray-400 hover:text-gray-500 focus:outline-none focus:ring-2 focus:ring-orange-500 focus:ring-offset-2"
                    onClick={onClose}
                  >
                    <span className="sr-only">Close</span>
                    <X className="h-6 w-6" aria-hidden="true" />
                  </button>
                </div>

                <div className="sm:mx-auto sm:w-full">
                  <DialogTitle as="h3" className="text-xl font-semibold leading-6 text-gray-900 mb-4 text-center">
                    {documentType ? t(titleKey) : ''}
                  </DialogTitle>

                  <div
                    className="mt-2 text-sm text-gray-500 max-h-[60vh] overflow-y-auto px-2 border-t border-b border-gray-100 py-4 scroll-smooth"
                    onScroll={handleScroll}
                  >
                    {loading ? (
                      <div className="flex justify-center py-8">
                        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-orange-600"></div>
                      </div>
                    ) : (
                      <MarkdownContent content={content} theme="light" />
                    )}
                  </div>

                  {/* Info: (20260118 - Luphia) Interactive Buttons */}
                  <div className="mt-5 sm:mt-6 flex gap-3">
                    <button
                      type="button"
                      className="flex-1 w-full justify-center rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50 hover:text-red-500 transition-all group"
                      onClick={onClose}
                    >
                      <span className="inline-block transition-transform duration-300 group-hover:animate-[shake_0.5s_ease-in-out_infinite]">
                        {t('common.close')}
                      </span>
                    </button>
                    <button
                      type="button"
                      disabled={!hasScrolledToBottom}
                      className={`flex-1 w-full justify-center rounded-md px-3 py-2 text-sm font-semibold text-white shadow-sm transition-all group
                        ${hasScrolledToBottom
                          ? 'bg-orange-600 hover:bg-orange-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-orange-600'
                          : 'bg-gray-300 cursor-not-allowed'}
                      `}
                      onClick={onClose}
                    >
                      <div className="flex items-center justify-center gap-2">
                        {hasScrolledToBottom && (
                          <Smile className="w-5 h-5 group-hover:animate-[nod_0.5s_ease-in-out_infinite]" />
                        )}
                        <span>{t('common.agree')}</span>
                      </div>
                    </button>
                  </div>
                  {!hasScrolledToBottom && !loading && (
                    <div className="absolute bottom-20 left-0 right-0 flex justify-center pointer-events-none">
                      <span className="bg-orange-50/90 backdrop-blur-sm text-orange-600 px-4 py-2 rounded-full shadow-lg border border-orange-200 text-sm font-bold animate-bounce">
                        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
                        {t('auth_modal.scroll_to_agree' as any)}
                      </span>
                    </div>
                  )}
                </div>
              </DialogPanel>
            </TransitionChild>
          </div>
        </div>
        <style>{`
          @keyframes shake {
            0%, 100% { transform: translateX(0); }
            25% { transform: translateX(-4px); }
            75% { transform: translateX(4px); }
          }
          @keyframes nod {
            0%, 100% { transform: translateY(0); }
            25% { transform: translateY(-4px); }
            75% { transform: translateY(4px); }
          }
        `}</style>
      </Dialog>
    </Transition>
  );
}
