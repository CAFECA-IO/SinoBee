'use client';

import React from 'react';
import { motion } from 'framer-motion';
import GoldDust from '@/components/landing/gold_dust';

const WireframeBee = () => (
  <svg width="400" height="400" viewBox="0 0 512 512" fill="none" xmlns="http://www.w3.org/2000/svg" className="drop-shadow-[0_0_15px_rgba(212,175,55,0.5)]">
    <g transform="translate(64, 64) scale(0.75)">
      {/* Wireframe Style: Thin lines, gold stroke, slightly transparent fill to simmer */}

      {/* Wing - Semi-transparent golden glow */}
      <path d="M260 240 C360 140 460 70 360 40 C280 10 200 140 230 240"
        stroke="url(#wireframe-gradient)" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round"
        fill="rgba(212, 175, 55, 0.05)" />

      {/* Body Segments - Giving a 3D feel with multiple wireframe lines */}
      <path d="M160 220 C130 220 110 240 110 270 C110 300 130 320 160 320 C180 320 200 300 210 280 L230 250 Z"
        stroke="url(#wireframe-gradient)" strokeWidth="4" fill="none" />
      <path d="M210 280 C230 280 250 320 260 330 C300 380 400 380 430 330 C460 280 400 230 360 230 C320 230 260 230 230 250"
        stroke="url(#wireframe-gradient)" strokeWidth="4" fill="rgba(0, 10, 18, 0.8)" />

      {/* Internal Wireframe Lines for volume */}
      <path d="M230 250 C260 250 300 250 360 230" stroke="url(#wireframe-gradient)" strokeWidth="2" strokeDasharray="4 4" opacity="0.5" />
      <path d="M260 330 C300 360 380 360 430 330" stroke="url(#wireframe-gradient)" strokeWidth="2" strokeDasharray="4 4" opacity="0.5" />
      <path d="M290 245 C290 245 310 290 290 355" stroke="url(#wireframe-gradient)" strokeWidth="4" strokeLinecap="round" />
      <path d="M350 235 C350 235 370 280 350 345" stroke="url(#wireframe-gradient)" strokeWidth="4" strokeLinecap="round" />

      {/* Antennae */}
      <path d="M130 240 C110 190 80 170 60 190" stroke="url(#wireframe-gradient)" strokeWidth="4" strokeLinecap="round" />

      {/* Eye */}
      <circle cx="145" cy="260" r="8" fill="var(--color-sinobee-gold-foil)" />
    </g>
    <defs>
      <linearGradient id="wireframe-gradient" x1="0" y1="0" x2="512" y2="512" gradientUnits="userSpaceOnUse">
        <stop offset="0" stopColor="#F4C430" />
        <stop offset="1" stopColor="#D4AF37" />
      </linearGradient>
    </defs>
  </svg>
);

const HeroSection = () => {
  return (
    <div className="relative min-h-screen w-full overflow-hidden bg-[var(--color-sinobee-midnight)] flex items-center justify-center">
      <GoldDust />

      <div className="container mx-auto px-6 relative z-10 grid grid-cols-1 lg:grid-cols-2 gap-12 items-center h-full pt-20">

        {/* Left: Copywriting */}
        <motion.div
          initial={{ opacity: 0, x: -50 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 1 }}
          className="flex flex-col gap-8 text-center lg:text-left"
        >
          <div>
            <h1 className="text-4xl md:text-5xl lg:text-6xl font-bold text-white leading-tight tracking-tight">
              SinoBee：
              <span className="block mt-2 text-transparent bg-clip-text bg-gradient-to-r from-[var(--color-sinobee-gold-foil)] to-[#FBE8A6]">
                點石成金的智慧夥伴
              </span>
            </h1>
            <p className="mt-6 text-gray-300 text-lg md:text-xl font-light tracking-wide max-w-xl mx-auto lg:mx-0">
              專屬您的 AI 智能理財守護者，以數據築巢，為財富釀蜜。
            </p>
          </div>

          <div className="flex justify-center lg:justify-start">
            <button className="relative px-8 py-3 bg-gradient-to-r from-[var(--color-sinobee-gold-foil)] to-[#b88a00] text-[var(--color-sinobee-midnight)] font-bold text-lg rounded-sm shadow-[0_0_15px_rgba(212,175,55,0.3)] hover:shadow-[0_0_25px_rgba(212,175,55,0.6)] transition-all transform hover:-translate-y-1">
              開始體驗
            </button>
          </div>
        </motion.div>

        {/* Right: Visual (Honeycomb + Bee) */}
        <motion.div
          initial={{ opacity: 0, scale: 0.8 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 1, delay: 0.2 }}
          className="relative flex justify-center items-center"
        >
          {/* Hexagonal Floating Island Base */}
          <div className="absolute w-[500px] h-[500px] bg-[radial-gradient(circle,rgba(0,26,51,0.8)_0%,rgba(0,0,0,0)_70%)] blur-2xl -z-10"></div>

          <div className="relative w-[400px] h-[400px]">
            {/* Background Decor Hexagons */}
            <svg className="absolute top-0 right-0 w-full h-full animate-[spin_60s_linear_infinite]" viewBox="0 0 100 100" overflow="visible">
              <path d="M50 0 L93 25 V75 L50 100 L7 75 V25 Z" fill="none" stroke="rgba(212,175,55,0.1)" strokeWidth="0.5" transform="scale(1.2) translate(-10, -10)" />
              <path d="M50 0 L93 25 V75 L50 100 L7 75 V25 Z" fill="none" stroke="rgba(212,175,55,0.2)" strokeWidth="0.5" transform="rotate(30 50 50) scale(0.8) translate(12, 12)" />
            </svg>

            {/* Main 3D Bee */}
            <motion.div
              animate={{ y: [0, -15, 0] }}
              transition={{ duration: 6, repeat: Infinity, ease: "easeInOut" }}
              className="flex justify-center items-center w-full h-full"
            >
              <WireframeBee />
            </motion.div>
          </div>
        </motion.div>
      </div>
    </div>
  );
};

export default HeroSection;
