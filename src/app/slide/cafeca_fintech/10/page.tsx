import { Sparkles } from 'lucide-react';

export default function CafecaFintechSlide7() {
  return (
    <div className="h-full w-full bg-slate-900 text-white relative flex items-center justify-center overflow-hidden">

      {/* Info: (20260122 - Luphia) Background Animation / Effect */}
      <div className="absolute inset-0 z-0">
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-sky-500/10 rounded-full blur-[120px] animate-pulse"></div>
        <div className="absolute top-0 right-0 w-[500px] h-[500px] bg-orange-500/10 rounded-full blur-[100px]"></div>
        <div className="absolute bottom-0 left-0 w-[500px] h-[500px] bg-sky-600/10 rounded-full blur-[100px]"></div>
      </div>

      <div className="relative z-10 text-center space-y-8">

        <div className="inline-flex items-center justify-center w-20 h-20 rounded-full bg-gradient-to-br from-sky-500 to-sky-700 shadow-[0_0_40px_rgba(14,165,233,0.4)] mb-8">
          <Sparkles className="text-white" size={40} />
        </div>

        <h2 className="text-7xl font-extrabold tracking-tight text-white pb-4 drop-shadow-[0_0_15px_rgba(255,255,255,0.3)]">
          Thank You
        </h2>

        <div className="h-1 w-24 bg-gradient-to-r from-sky-500 to-orange-500 rounded-full mx-auto"></div>

        <p className="text-2xl text-slate-100 font-light tracking-wide max-w-2xl mx-auto leading-relaxed drop-shadow-md">
          Leading the future of fintech with security, intelligence, and innovation.
        </p>

        <div className="pt-12">
          <div className="text-sky-400 font-bold text-lg tracking-[0.2em] uppercase">CAFECA Fintech</div>
          <div className="text-slate-400 text-sm mt-2 font-mono">2026 iSunFA Corp.</div>
        </div>
      </div>

    </div>
  );
}
