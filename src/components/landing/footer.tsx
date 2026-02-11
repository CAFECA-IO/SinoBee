'use client';

import React from 'react';
import Image from 'next/image';

const Footer = () => {
  return (
    <footer className="bg-[var(--color-sinobee-blue)] text-white/60 py-12 border-t border-white/10">
      <div className="container mx-auto px-6 flex flex-col md:flex-row justify-between items-center gap-6">

        {/* Brand & Logo */}
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 relative">
            <Image
              src="/logo.png"
              alt="SinoBee Logo"
              fill
              className="object-contain"
            />
          </div>
          <div>
            <h5 className="font-bold text-white">SinoBee</h5>
            <p className="text-xs">Your AI Wealth Nectarist</p>
          </div>
        </div>

        {/* Legal Notice */}
        <div className="text-center md:text-right text-xs">
          <p className="mb-2">
            Academic Research & Technical Reference Only.
            <br />
            All trademarks and IP (including Sinobee) are owned by SinoPac Financial Holdings.
          </p>
          <div className="flex gap-4 justify-center md:justify-end mt-2">
            <a href="https://bank.sinopac.com" target="_blank" rel="noopener noreferrer" className="hover:text-white transition-colors">永豐銀行</a>
            <span className="w-[1px] h-3 bg-white/20 self-center"></span>
            <a href="https://www.sinotrade.com.tw" target="_blank" rel="noopener noreferrer" className="hover:text-white transition-colors">永豐金證券</a>
          </div>
        </div>

      </div>
    </footer>
  );
};

export default Footer;
