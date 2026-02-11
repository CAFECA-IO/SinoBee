import { Target, Flag, Cpu, Coins } from 'lucide-react';

export default function CafecaFintechSlide8() {
  return (
    <div className="h-full w-full bg-slate-50 relative flex flex-col overflow-hidden">

      {/* Info: (20260123) Background Elements */}
      <div className="absolute inset-0 z-0">
        <div className="absolute -top-[300px] -right-[300px] w-[800px] h-[800px] bg-sky-100/50 rounded-full blur-[100px]"></div>
        <div className="absolute -bottom-[300px] -left-[300px] w-[800px] h-[800px] bg-indigo-50/50 rounded-full blur-[100px]"></div>
        <div className="absolute inset-0 bg-[url('https://www.transparenttextures.com/patterns/cubes.png')] opacity-[0.03]"></div>
      </div>

      {/* Info: (20260123) Header */}
      <div className="relative z-10 w-full px-16 pt-12 mb-6 flex justify-between items-end">
        <div>
          <div className="flex items-center gap-4 mb-2">
            <div className="w-12 h-1 bg-sky-600 rounded-full"></div>
            <span className="text-sky-700 font-bold tracking-[0.2em] uppercase text-sm">Strategic Milestone</span>
          </div>
          <h1 className="text-5xl font-extrabold text-slate-800 leading-tight">
            募資目標
          </h1>
        </div>

        <div className="flex items-center gap-6 bg-white py-4 px-8 rounded-2xl shadow-lg border border-sky-100 animate-fade-in-up">
          <div className="bg-sky-100 p-3 rounded-xl text-sky-600">
            <Coins size={32} />
          </div>
          <div>
            <div className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Fundraising Offer</div>
            <div className="text-3xl font-extrabold text-slate-800">15 萬股 <span className="text-base text-slate-500 font-medium normal-case ml-1">Shares</span></div>
          </div>
        </div>
      </div>

      {/* Info: (20260123) Main Content */}
      <div className="relative z-10 flex-1 px-16 pb-12 flex gap-16 items-center">

        {/* Info: (20260123) Left: Primary Goals Column */}
        <div className="w-[48%] flex flex-col gap-6">

          {/* Info: (20260123) Goal 1: Compliance */}
          <div className="bg-white p-6 rounded-3xl shadow-lg border border-slate-100 relative group overflow-hidden">
            <div className="absolute top-0 right-0 w-32 h-32 bg-sky-50 rounded-bl-[100px] -mr-8 -mt-8 transition-transform group-hover:scale-110"></div>
            <div className="relative z-10">
              <div className="w-12 h-12 bg-sky-100 rounded-2xl flex items-center justify-center text-sky-600 mb-4 group-hover:rotate-12 transition-transform">
                <Target size={24} />
              </div>
              <h2 className="text-xl font-bold text-slate-800 mb-2 leading-tight">
                獲取企業融資系統<br />合規營運資格
              </h2>
              <p className="text-slate-500 text-sm leading-relaxed text-justify">
                不僅是軟體銷售，我們的目標是成為合規的金融基礎設施提供商，為企業提供直接、高效的融資運營服務。
              </p>
            </div>
          </div>

          {/* Info: (20260123) Goal 2: Compute Lease */}
          <div className="bg-white p-6 rounded-3xl shadow-lg border border-slate-100 relative group overflow-hidden">
            <div className="absolute top-0 right-0 w-32 h-32 bg-indigo-50 rounded-bl-[100px] -mr-8 -mt-8 transition-transform group-hover:scale-110"></div>
            <div className="relative z-10">
              <div className="w-12 h-12 bg-indigo-100 rounded-2xl flex items-center justify-center text-indigo-600 mb-4 group-hover:rotate-12 transition-transform">
                <Cpu size={24} />
              </div>
              <h2 className="text-xl font-bold text-slate-800 mb-2 leading-tight">
                500 萬模型訓練算力租賃
              </h2>
              <p className="text-slate-500 text-sm leading-relaxed text-justify">
                確保 AI 模型持續演進的核心動力，建立自主且強大的運算基礎設施，支持未來金融模型的深度訓練與優化。
              </p>
            </div>
          </div>

        </div>

        {/* Info: (20260123) Right: Target Markets Grid */}
        <div className="flex-1 grid grid-cols-2 gap-6">
          {[
            { name: '臺灣', en: 'Taiwan', color: 'bg-emerald-500' },
            { name: '香港', en: 'Hong Kong', color: 'bg-purple-500' },
            { name: '新加坡', en: 'Singapore', color: 'bg-sky-500' },
            { name: '日本', en: 'Japan', color: 'bg-rose-500' },
          ].map((market) => (
            <div key={market.name} className="bg-white p-6 rounded-2xl shadow-sm border border-slate-200 hover:shadow-md hover:border-slate-300 transition-all group flex items-center gap-4">
              <div className={`w-12 h-12 ${market.color} bg-opacity-10 rounded-xl flex items-center justify-center text-${market.color.split('-')[1]}-600 group-hover:scale-110 transition-transform`}>
                <Flag size={20} className={market.color.replace('bg-', 'text-')} />
              </div>
              <div>
                <h3 className="text-xl font-bold text-slate-800">{market.name}</h3>
                <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">{market.en}</span>
              </div>
            </div>
          ))}
        </div>

      </div>

      <div className="absolute bottom-6 right-12 text-slate-300 text-[10px] font-mono z-10">
        iSunFA • FUNDRAISING & EXPANSION
      </div>

    </div>
  );
}
