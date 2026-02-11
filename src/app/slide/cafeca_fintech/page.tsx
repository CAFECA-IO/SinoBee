'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { ChevronLeft, ChevronRight, MonitorPlay, Download, Grid, Maximize2 } from 'lucide-react';
import Link from 'next/link';
import CafecaFintechSlide1 from '@/app/slide/cafeca_fintech/1/page';
import CafecaFintechSlide2 from '@/app/slide/cafeca_fintech/2/page';
import CafecaFintechSlide3 from '@/app/slide/cafeca_fintech/3/page';
import CafecaFintechSlide4 from '@/app/slide/cafeca_fintech/4/page';
import CafecaFintechSlide5 from '@/app/slide/cafeca_fintech/5/page';
import CafecaFintechSlide6 from '@/app/slide/cafeca_fintech/6/page';
import CafecaFintechSlide7 from '@/app/slide/cafeca_fintech/7/page';
import CafecaFintechSlide8 from '@/app/slide/cafeca_fintech/8/page';
import CafecaFintechSlide9 from '@/app/slide/cafeca_fintech/9/page';
import CafecaFintechSlide10 from '@/app/slide/cafeca_fintech/10/page';

export default function CafecaFintechSlideBrowser() {
  const [currentSlide, setCurrentSlide] = useState(1);
  const totalSlides = 10;
  const containerRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);

  // Info: (20260122 - Luphia) Dynamic scaling logic
  const [mobileScale, setMobileScale] = useState(0.3);

  useEffect(() => {
    const handleResize = () => {
      // Info: (20260122 - Luphia) Desktop Preview Calc
      if (containerRef.current) {
        const { width, height } = containerRef.current.getBoundingClientRect();
        const targetWidth = 1280;
        const targetHeight = 720;
        const scaleX = width / targetWidth;
        const scaleY = height / targetHeight;
        const newScale = Math.min(scaleX, scaleY);
        setScale(newScale);
      }

      // Info: (20260122 - Luphia) Mobile List Calc
      if (window.innerWidth < 768) {
        const w = window.innerWidth;
        const targetW = 1280;
        setMobileScale(w / targetW);
      }
    };

    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // Info: (20260122 - Luphia) Slide Components Map
  const SlideComponents: { [key: number]: React.ComponentType } = {
    1: CafecaFintechSlide1,
    2: CafecaFintechSlide2,
    3: CafecaFintechSlide3,
    4: CafecaFintechSlide4,
    5: CafecaFintechSlide5,
    6: CafecaFintechSlide6,
    7: CafecaFintechSlide7,
    8: CafecaFintechSlide8,
    9: CafecaFintechSlide9,
    10: CafecaFintechSlide10,
  };

  const CurrentSlideComponent = SlideComponents[currentSlide];

  const slideTitles: { [key: number]: string } = {
    1: 'Cover: CAFECA Fintech',
    2: 'Corporate Vision',
    3: 'Company Profile',
    4: 'Core Technology',
    5: 'Core Product: iSunFA',
    6: 'R&D Investment',
    7: 'Equity Structure',
    8: 'Fundraising Goals',
    9: 'Regulatory Sandbox',
    10: 'Thank You',
  };

  const nextSlide = useCallback(() => {
    setCurrentSlide(c => (c < totalSlides ? c + 1 : c));
  }, [totalSlides]);

  const prevSlide = useCallback(() => {
    setCurrentSlide(c => (c > 1 ? c - 1 : c));
  }, []);

  const goToSlide = (id: number) => {
    setCurrentSlide(id);
  };

  // Info: (20260122 - Luphia) Keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowRight') nextSlide();
      if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') prevSlide();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [nextSlide, prevSlide]);

  return (
    <div className="flex flex-col h-screen bg-neutral-900 text-white overflow-hidden">

      {/* Info: (20260122 - Luphia) Universal Header */}
      <div className="h-14 border-b border-neutral-800 flex items-center justify-between px-4 bg-neutral-900 z-20 flex-shrink-0">
        <Link href="/slide" className="flex items-center gap-3 hover:opacity-80 transition-opacity">
          <div className="bg-sky-600 p-1.5 rounded-lg">
            <MonitorPlay size={20} className="text-white" />
          </div>
          <div>
            <h1 className="text-sm font-bold text-gray-200">CAFECA Fintech Presentation</h1>
            <p className="text-xs text-gray-500">v1.0.0 • 2026 iSunFA</p>
          </div>
        </Link>

        {/* Info: (20260122 - Luphia) Desktop Controls - Hidden on Mobile */}
        <div className="hidden md:flex items-center gap-2">
          <span className="text-xs font-mono text-gray-500 mr-2">
            {currentSlide} / {totalSlides}
          </span>
          <button
            onClick={prevSlide}
            disabled={currentSlide === 1}
            className="p-2 hover:bg-neutral-800 rounded-full disabled:opacity-30 transition-colors"
          >
            <ChevronLeft size={20} />
          </button>
          <button
            onClick={nextSlide}
            disabled={currentSlide === totalSlides}
            className="p-2 hover:bg-neutral-800 rounded-full disabled:opacity-30 transition-colors"
          >
            <ChevronRight size={20} />
          </button>
        </div>

        {/* Info: (20260122 - Luphia) Right Side Actions - Visible on both but adjusted */}
        <div className="flex items-center gap-3">
          <Link href="/slide/cafeca_fintech/print" target="_blank" className="hidden md:block">
            <button className="flex items-center gap-2 px-3 py-1.5 bg-neutral-800 hover:bg-neutral-700 rounded-md text-xs font-medium transition-colors border border-neutral-700 text-gray-200">
              <Download size={14} />
              <span>PDF</span>
            </button>
          </Link>
          <Link href={`/slide/cafeca_fintech/${currentSlide}`} target="_blank" className="hidden md:block">
            <button className="p-2 hover:bg-neutral-800 rounded-md text-gray-400 hover:text-white transition-colors">
              <Maximize2 size={18} />
            </button>
          </Link>
        </div>
      </div>

      {/* Info: (20260122 - Luphia) Mobile Vertical Scroll View - Visible only on mobile */}
      <div
        className="md:hidden flex-1 overflow-y-auto bg-neutral-900 scroll-smooth"
        onScroll={(e) => {
          const target = e.currentTarget;
          // Info: (20260122 - Luphia) 35vh is approx 35% of clientHeight
          const paddingOffset = target.clientHeight * 0.35;
          const slideHeight = (720 * mobileScale) + 16;

          // Info: (20260122 - Luphia) Center of the Viewport relative to content top (0)
          const scrollCenter = target.scrollTop + (target.clientHeight / 2);

          // Info: (20260122 - Luphia) Adjust for the top padding so 0 starts at the first slide
          const relativePosition = scrollCenter - paddingOffset;

          const index = Math.floor(relativePosition / slideHeight) + 1;
          const safeIndex = Math.max(1, Math.min(totalSlides, index));

          if (safeIndex !== currentSlide) {
            setCurrentSlide(safeIndex);
          }
        }}
      >
        <div className="flex flex-col items-center gap-4 py-[35vh] min-h-screen">
          {Array.from({ length: totalSlides }, (_, i) => i + 1).map((id) => {
            const distance = Math.abs(id - currentSlide);
            // Info: (20260122 - Luphia) Only load 3 slides: current +/- 1
            const shouldRender = distance <= 1;

            /**
             * Info: (20260122 - Luphia) Visual Effects based on distance
             * 0: opacity-100 blur-0 grayscale-0
             * 1: opacity-40 blur-sm grayscale
             */
            const opacityClass = distance === 0 ? 'opacity-100 scale-100' :
              'opacity-40 scale-95 blur-[2px] grayscale';

            const Component = SlideComponents[id];

            return (
              <div key={id} className={`w-full relative overflow-hidden transition-all duration-500 ease-out ${opacityClass}`}
                style={{ height: 720 * mobileScale }}>

                {/* Info: (20260122 - Luphia) Scale Wrapper */}
                <div
                  style={{
                    transform: `scale(${mobileScale})`,
                    transformOrigin: 'top left',
                    width: 1280,
                    height: 720
                  }}
                  className="bg-white shadow-xl rounded-lg"
                >
                  <div className="w-full h-full [&>div]:!min-h-0 [&>div]:!h-full [&>div]:!bg-transparent [&>div]:!p-0">
                    {shouldRender ? <Component /> : <div className="w-full h-full bg-neutral-800/50 animate-pulse" />}
                  </div>
                </div>

                {/* Info: (20260122 - Luphia) Overlay Page Number for Mobile */}
                <div className={`absolute top-2 right-2 px-2 py-1 rounded-full z-10 pointer-events-none transition-opacity duration-300 ${distance === 0 ? 'bg-orange-600 text-white shadow-lg' : 'bg-black/50 text-gray-400'}`}>
                  <span className="text-[10px] font-bold">{id}</span>
                  <span className="text-[8px] opacity-80">/{totalSlides}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Info: (20260122 - Luphia) Desktop Layout - Hidden on Mobile */}
      <div className="hidden md:flex flex-1 overflow-hidden">
        {/* Info: (20260122 - Luphia) Simple flex container for desktop content since header is moved out */}

        {/* Info: (20260122 - Luphia) Main Layout: Content (Left) + Navigator (Right) */}
        <div className="flex-1 flex overflow-hidden">
          {/* Info: (20260122 - Luphia) Preview Area */}
          <div className="flex-1 bg-neutral-950 flex items-center justify-center p-8 relative overflow-hidden" ref={containerRef}>
            {/* Info: (20260122 - Luphia) Scaled Content Wrapper */}
            <div
              style={{
                transform: `scale(${scale})`,
                width: 1280,
                height: 720,
                transformOrigin: 'center center'
              }}
              className="bg-white shadow-2xl flex-shrink-0 relative overflow-hidden ring-1 ring-neutral-800"
            >
              <div className="w-full h-full [&>div]:!min-h-0 [&>div]:!h-full [&>div]:!bg-transparent [&>div]:!p-0">
                <CurrentSlideComponent />
              </div>
            </div>
          </div>

          {/* Info: (20260122 - Luphia) Side Navigator (Right) */}
          <div className="w-64 border-l border-neutral-800 bg-neutral-900 flex flex-col flex-shrink-0">
            <div className="px-4 py-3 text-xs text-gray-500 font-bold uppercase tracking-wider flex items-center gap-2 border-b border-neutral-800">
              <Grid size={12} />
              <span>Navigator</span>
            </div>
            <div className="flex-1 overflow-y-auto flex flex-col gap-4 p-4 scrollbar-hide">
              {Array.from({ length: totalSlides }, (_, i) => i + 1).map((id) => (
                <button
                  key={id}
                  onClick={() => goToSlide(id)}
                  className={`flex-shrink-0 w-full aspect-video rounded-lg border-2 transition-all duration-200 relative group overflow-hidden ${currentSlide === id
                    ? 'border-sky-500 ring-2 ring-sky-500/20'
                    : 'border-neutral-800 hover:border-neutral-700 opacity-60 hover:opacity-100'
                    }`}
                >
                  {/* Info: (20260122 - Luphia) Mini Preview Mock */}
                  <div className="absolute inset-0 bg-white">
                    <div className="w-full h-full flex items-center justify-center bg-gray-50 text-gray-300 text-xs font-mono">
                      LOGO
                    </div>
                    {/* Info: (20260122 - Luphia) Overlay Title */}
                    <div className="absolute bottom-0 inset-x-0 bg-neutral-900/90 p-2 text-left">
                      <div className="flex justify-between items-center mb-0.5">
                        <span className="text-[10px] font-bold text-sky-500">#{id.toString().padStart(2, '0')}</span>
                      </div>
                      <div className="text-[10px] text-gray-300 font-medium truncate leading-tight">{slideTitles[id] || `Slide ${id}`}</div>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
