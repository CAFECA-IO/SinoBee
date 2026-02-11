'use client';

import React, { useState } from 'react';
import { useAuth } from '@/contexts/auth_context';
import AuthModal from '@/components/auth/auth_modal';
import { LockKeyhole, Loader2 } from 'lucide-react';

export default function SlideLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const [isAuthModalOpen, setIsAuthModalOpen] = useState(false);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <Loader2 className="animate-spin text-orange-600" size={48} />
      </div>
    );
  }

  if (!user) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 p-4">
        <div className="max-w-md w-full bg-white rounded-2xl shadow-xl p-8 text-center border border-gray-100">
          <div className="w-16 h-16 bg-orange-100 text-orange-600 rounded-full flex items-center justify-center mx-auto mb-6">
            <LockKeyhole size={32} />
          </div>

          <h2 className="text-2xl font-bold text-gray-900 mb-2">Login Required</h2>
          <p className="text-gray-500 mb-8">
            Please log in to view the presentation slides.
          </p>

          <button
            onClick={() => setIsAuthModalOpen(true)}
            className="w-full bg-orange-600 hover:bg-orange-500 text-white font-bold py-3 px-6 rounded-xl transition-all shadow-md hover:shadow-lg transform hover:-translate-y-0.5"
          >
            Login / Sign Up
          </button>

          <AuthModal
            isOpen={isAuthModalOpen}
            onClose={() => setIsAuthModalOpen(false)}
            onSuccess={() => setIsAuthModalOpen(false)}
          />
        </div>
      </div>
    );
  }

  return (
    <>
      {children}
    </>
  );
}
