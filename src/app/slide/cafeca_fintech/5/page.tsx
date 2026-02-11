import { Sparkles, Building2, ArrowDown } from 'lucide-react';

export default function CafecaFintechSlide5() {
  return (
    <div className="h-full w-full bg-slate-50 relative flex flex-col overflow-hidden">

      {/* Info: (20260123) Background Elements */}
      <div className="absolute inset-0 z-0">
        <div className="absolute top-0 w-full h-1/2 bg-sky-50"></div>
        <div className="absolute bottom-0 w-full h-1/2 bg-slate-900"></div>
      </div>

      {/* Info: (20260123) Header */}
      <div className="relative z-10 w-full px-16 pt-12 mb-8">
        <div className="flex items-center gap-4 mb-2">
          <div className="w-12 h-1 bg-sky-600 rounded-full"></div>
          <span className="text-sky-700 font-bold tracking-[0.2em] uppercase text-sm">核心產品</span>
        </div>
        <h1 className="text-5xl font-extrabold text-slate-800 leading-tight">
          iSunFA
        </h1>
      </div>

      <div className="relative z-10 flex-1 flex flex-col items-center justify-center px-16 pb-12 gap-8">

        {/* Info: (20260123) Top Card - The Surface */}
        <div className="w-full max-w-4xl bg-white rounded-3xl p-8 shadow-xl border border-slate-100 flex items-center gap-8 relative z-20 transform translate-y-4 hover:-translate-y-1 transition-transform duration-500">
          <div className="w-20 h-20 bg-sky-100 rounded-2xl flex items-center justify-center text-sky-600 shrink-0">
            <Sparkles size={40} />
          </div>
          <div className="flex-1">
            <div className="text-sm font-bold text-sky-600 uppercase tracking-widest mb-1">表面價值</div>
            <h2 className="text-3xl font-bold text-slate-800 mb-2">AI 智能會計軟體</h2>
            <p className="text-slate-500 text-lg leading-relaxed">由先進人工智能模型驅動的出納、自動化記帳、稅務合規與財務分析系統。</p>
          </div>
          <div className="text-right shrink-0">
            <span className="block text-4xl font-extrabold text-slate-200">10%</span>
            <span className="text-xs text-slate-400 font-medium uppercase tracking-wider">可見效用</span>
          </div>
        </div>

        {/* Info: (20260123) Connector */}
        <div className="relative z-30 flex flex-col items-center justify-center text-white/80 animate-bounce">
          <span className="text-xs font-mono font-bold tracking-widest mb-1 opacity-80">底層核心</span>
          <ArrowDown size={24} />
        </div>

        {/* Info: (20260123) Bottom Card - The Core Reality */}
        <div className="w-full max-w-5xl bg-gradient-to-r from-blue-900 via-indigo-900 to-slate-900 rounded-3xl p-10 shadow-2xl border border-white/10 flex items-center gap-10 relative z-20 hover:scale-[1.02] transition-transform duration-500 ring-1 ring-white/10">
          <div className="w-24 h-24 bg-white/10 backdrop-blur-sm rounded-2xl flex items-center justify-center text-sky-400 shrink-0 border border-white/20 shadow-[0_0_30px_rgba(56,189,248,0.3)]">
            <Building2 size={48} />
          </div>
          <div className="flex-1">
            <div className="flex items-center gap-3 mb-2">
              <div className="px-2 py-0.5 rounded bg-sky-500 text-white text-[10px] font-bold uppercase tracking-wider">金融牌照賦能</div>
              <div className="text-sm font-bold text-sky-400 uppercase tracking-widest opacity-80">合規後上線</div>
            </div>
            <h2 className="text-4xl font-bold text-white mb-3">銀行核心融資與放款系統</h2>
            <p className="text-slate-300 text-lg font-light leading-relaxed">
              透過財務分析技術實現<span className="text-white font-medium">銀行核心系統</span>，即時捕捉財務健康狀況，實現大規模融資與放款審核。
            </p>
          </div>
          <div className="text-right shrink-0">
            <span className="block text-5xl font-extrabold text-white/20">90%</span>
            <span className="text-xs text-white/40 font-medium uppercase tracking-wider">真實價值</span>
          </div>
        </div>

      </div>

      <div className="absolute bottom-6 right-12 text-white/20 text-[10px] font-mono z-10">
        CAFECA FINTECH • CORE PRODUCT
      </div>

    </div>
  );
}
