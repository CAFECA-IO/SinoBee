'use client';

import React from 'react';
import { motion } from 'framer-motion';

const features = [
  {
    title: 'Smart Savings',
    desc: '智能儲蓄',
    icon: (
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
      </svg>
    )
  },
  {
    title: 'Global Remittance',
    desc: '全球匯款',
    icon: (
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" />
        <path d="M2 12h20" />
        <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
      </svg>
    )
  },
  {
    title: 'Asset Analysis',
    desc: '資產分析',
    icon: (
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 3v18h18" />
        <path d="M18 17V9" />
        <path d="M13 17V5" />
        <path d="M8 17v-3" />
      </svg>
    )
  }
];

const HexCard = ({ feature, index }: { feature: typeof features[0], index: number }) => {
  return (
    <motion.div
      initial={{ opacity: 0, y: 50 }}
      whileInView={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: index * 0.2 }}
      viewport={{ once: true }}
      className="group relative w-[280px] h-[320px] mx-4 my-[-40px] md:my-0 flex items-center justify-center cursor-pointer"
    >
      {/* Hexagon shape CSS background */}
      <div style={{ clipPath: 'polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%)' }} className="absolute inset-0 bg-[var(--color-sinobee-blue)] transition-all duration-300 group-hover:bg-[#00264d] shadow-[0_10px_30px_rgba(0,0,0,0.5)]"></div>

      {/* Gold Border / Wavy Effect on Hover */}
      <div style={{ clipPath: 'polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%)' }} className="absolute inset-[2px] bg-[var(--color-sinobee-midnight)] z-10 flex flex-col items-center justify-center p-6 text-center overflow-hidden">

        {/* Background Liquid Wave (Simulated) */}
        <div className="absolute bottom-0 left-0 w-full h-0 bg-[var(--color-sinobee-gold-foil)] opacity-10 transition-all duration-500 group-hover:h-full z-0"></div>

        <div className="relative z-10 text-[var(--color-sinobee-gold-foil)] group-hover:scale-110 transition-transform duration-300 mb-4">
          {feature.icon}
        </div>
        <h3 className="relative z-10 text-xl font-bold text-white mb-2">{feature.title}</h3>
        <p className="relative z-10 text-sm text-gray-400">{feature.desc}</p>
      </div>

      {/* Decorative Outline */}
      <svg className="absolute inset-0 w-full h-full pointer-events-none z-20" viewBox="0 0 100 100" preserveAspectRatio="none">
        <path d="M50 0 L100 25 V75 L50 100 L0 75 V25 Z" fill="none" stroke="var(--color-sinobee-gold-foil)" strokeWidth="1" strokeOpacity="0.3" vectorEffect="non-scaling-stroke" />
      </svg>

    </motion.div>
  );
};

const FeatureHexGrid = () => {
  return (
    <section className="relative py-32 w-full bg-[var(--color-sinobee-midnight)] overflow-hidden">
      <div className="container mx-auto px-6">
        <motion.div
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          className="flex flex-col md:flex-row flex-wrap justify-center items-center gap-8 md:gap-4 lg:gap-8"
        >
          {features.map((feature, i) => (
            <HexCard key={i} feature={feature} index={i} />
          ))}
        </motion.div>
      </div>
    </section>
  );
};

export default FeatureHexGrid;
