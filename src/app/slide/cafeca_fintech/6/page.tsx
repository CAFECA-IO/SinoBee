import { TrendingUp, Building2, Cloud } from 'lucide-react';

export default function CafecaFintechSlide4() {
  return (
    <div className="h-full w-full bg-slate-50 relative flex overflow-hidden">

      {/* Info: (20260122 - Luphia) Background Decorations */}
      <div className="absolute top-0 right-0 w-[500px] h-[500px] bg-sky-100/50 rounded-full blur-[80px] -translate-y-1/2 translate-x-1/2"></div>
      <div className="absolute bottom-0 left-0 w-[400px] h-[400px] bg-orange-100/50 rounded-full blur-[80px] translate-y-1/2 -translate-x-1/3"></div>

      <div className="w-full max-w-[1280px] mx-auto px-16 py-12 relative z-10 flex flex-col h-full">

        {/* Info: (20260122 - Luphia) Header */}
        <div className="mb-8 flex flex-col items-center">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-sky-100 text-sky-700 text-xs font-bold tracking-wider mb-4 border border-sky-200">
            <TrendingUp size={14} />
            R&D INVESTMENT
          </div>
          <h2 className="text-5xl font-extrabold text-slate-900 tracking-tight">投入研發</h2>
          <div className="h-1 w-20 bg-sky-500 rounded-full mt-6"></div>
        </div>

        {/* Info: (20260122 - Luphia) Content Area - Cards */}
        <div className="flex-1 flex items-center justify-center gap-12 px-12">

          {/* Info: (20260122 - Luphia) Card 1: Cafeca */}
          <div className="flex-1 max-w-sm h-[420px] bg-white rounded-[2rem] shadow-xl border border-slate-100 relative group overflow-hidden hover:-translate-y-2 transition-transform duration-300">
            <div className="absolute top-0 inset-x-0 h-2 bg-sky-500"></div>

            <div className="p-8 flex flex-col h-full items-center text-center">
              <div className="w-20 h-20 bg-sky-50 rounded-2xl flex items-center justify-center text-sky-600 mb-6 shadow-sm group-hover:scale-110 transition-transform duration-300">
                <Building2 size={36} />
              </div>

              <h3 className="text-2xl font-bold text-slate-800 mb-2">卡菲卡金融科技</h3>
              <p className="text-xs text-slate-500 font-medium uppercase tracking-wide mb-8">Cafeca Fintech Co., Ltd.</p>

              <div className="mt-auto w-full">
                <div className="text-sm text-slate-500 mb-1 font-medium">投入金額</div>
                <div className="text-4xl font-extrabold text-sky-600 font-mono tracking-tight flex justify-center items-baseline">
                  <span className="text-xl mr-1 text-slate-400">$</span>
                  15,000,000
                </div>
                <div className="text-xs text-slate-400 mt-2 font-mono">TWD</div>
              </div>
            </div>
          </div>

          {/* Info: (20260122 - Luphia) Divider / Connector */}
          <div className="text-slate-300">
            <div className="w-16 h-[2px] bg-slate-200 rounded-full"></div>
          </div>

          {/* Info: (20260122 - Luphia) Card 2: Sunshine Cloud */}
          <div className="flex-1 max-w-sm h-[420px] bg-white rounded-[2rem] shadow-xl border border-slate-100 relative group overflow-hidden hover:-translate-y-2 transition-transform duration-300">
            <div className="absolute top-0 inset-x-0 h-2 bg-orange-500"></div>

            <div className="p-8 flex flex-col h-full items-center text-center">
              <div className="w-20 h-20 bg-orange-50 rounded-2xl flex items-center justify-center text-orange-600 mb-6 shadow-sm group-hover:scale-110 transition-transform duration-300">
                <Cloud size={36} />
              </div>

              <h3 className="text-2xl font-bold text-slate-800 mb-2">台灣陽光雲</h3>
              <p className="text-xs text-slate-500 font-medium uppercase tracking-wide mb-8">Taiwan Sunshine Cloud Co., Ltd.</p>

              <div className="mt-auto w-full">
                <div className="text-sm text-slate-500 mb-1 font-medium">投入金額</div>
                <div className="text-4xl font-extrabold text-orange-500 font-mono tracking-tight flex justify-center items-baseline">
                  <span className="text-xl mr-1 text-slate-400">$</span>
                  48,000,000
                </div>
                <div className="text-xs text-slate-400 mt-2 font-mono">TWD</div>
              </div>
            </div>
          </div>

        </div>

      </div>
    </div>
  );
}
