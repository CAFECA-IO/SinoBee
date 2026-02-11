import { PieChart, Users, ArrowRightLeft, ScrollText } from 'lucide-react';

export default function CafecaFintechSlide5() {
  return (
    <div className="h-full w-full bg-slate-50 flex overflow-hidden relative">

      <div className="absolute top-0 right-0 w-[400px] h-full bg-sky-50 -skew-x-12 translate-x-1/2 transform origin-top"></div>

      {/* Info: (20260122 - Luphia) Left Content */}
      <div className="flex-1 p-16 flex flex-col justify-center relative z-10">
        <div className="mb-12">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-sky-100 text-sky-700 text-xs font-bold tracking-wider mb-6">
            <PieChart size={14} />
            EQUITY STRUCTURE
          </div>
          <h2 className="text-5xl font-extrabold text-slate-900 mb-6">股權結構</h2>
          <p className="text-slate-600 text-lg max-w-xl leading-relaxed">
            穩健的股權架構與創新的研發成果交換機制，奠定公司長遠發展的基石。
          </p>
        </div>

        <div className="grid grid-cols-1 gap-8 max-w-2xl">

          {/* Info: (20260122 - Luphia) Block 1: Issued Shares */}
          <div className="flex items-start gap-6 bg-white p-8 rounded-2xl shadow-sm border border-slate-200">
            <div className="w-12 h-12 bg-sky-100 rounded-full flex items-center justify-center text-sky-600 shrink-0">
              <Users size={24} />
            </div>
            <div>
              <h3 className="text-xl font-bold text-slate-900 mb-2">已發行股份</h3>
              <div className="flex items-baseline gap-2 mb-2">
                <span className="text-4xl font-bold text-sky-600 font-mono">30</span>
                <span className="text-sm text-slate-500 font-medium">萬股</span>
              </div>
              <p className="text-slate-600 text-sm">由自身與家人持有</p>
            </div>
          </div>

          {/* Info: (20260122 - Luphia) Block 2: Stock Options */}
          <div className="flex items-start gap-6 bg-white p-8 rounded-2xl shadow-sm border border-slate-200 relative overflow-hidden">
            <div className="absolute top-0 right-0 w-24 h-24 bg-orange-50 rounded-bl-[100px] z-0"></div>
            <div className="w-12 h-12 bg-orange-100 rounded-full flex items-center justify-center text-orange-600 shrink-0 z-10">
              <ArrowRightLeft size={24} />
            </div>
            <div className="z-10">
              <h3 className="text-xl font-bold text-slate-900 mb-2">研發成果交換選擇權</h3>
              <div className="flex items-baseline gap-2 mb-2">
                <span className="text-4xl font-bold text-orange-600 font-mono">100</span>
                <span className="text-sm text-slate-500 font-medium">萬股</span>
                <span className="text-xs text-slate-400 ml-2 bg-slate-100 px-2 py-1 rounded">@ 20 元 / 股</span>
              </div>
              <p className="text-slate-600 text-sm mb-4">透過選擇權交換研發成果</p>

              <div className="flex items-center gap-2 text-xs font-bold text-slate-500 bg-slate-50 px-3 py-2 rounded-lg border border-slate-100 w-fit">
                <ScrollText size={14} />
                持有者：台灣陽光雲有限公司
              </div>
            </div>
          </div>

        </div>
      </div>

      {/* Info: (20260122 - Luphia) Right Graphic Area */}
      <div className="w-[45%] bg-slate-900 relative flex items-center justify-center p-12 overflow-hidden">
        <div className="absolute inset-0 opacity-20 bg-[radial-gradient(circle_at_center,_var(--tw-gradient-stops))] from-sky-500 via-slate-900 to-slate-900"></div>

        {/* Info: (20260122 - Luphia) Visualization Diagram */}
        <div className="relative w-full max-w-md aspect-square">
          {/* Center Circle */}
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-48 h-48 bg-slate-800 rounded-full border-4 border-sky-500/30 flex items-center justify-center z-10 shadow-[0_0_50px_rgba(14,165,233,0.2)]">
            <div className="text-center">
              <div className="text-sky-400 font-bold text-xl tracking-wider">CAFECA</div>
              <div className="text-slate-500 text-[10px]">EQUITY</div>
            </div>
          </div>

          {/* Orbit Item 1 */}
          <div className="absolute top-1/2 left-1/2 w-full h-full -translate-x-1/2 -translate-y-1/2 animate-[spin_10s_linear_infinite]">
            <div className="absolute top-0 left-1/2 -translate-x-1/2 -translate-y-4 w-16 h-16 bg-white rounded-full flex items-center justify-center shadow-lg">
              <Users size={24} className="text-sky-600" />
            </div>
          </div>

          {/* Orbit Item 2 */}
          <div className="absolute top-1/2 left-1/2 w-80 h-80 -translate-x-1/2 -translate-y-1/2 rounded-full border border-dashed border-slate-700 animate-[spin_15s_linear_infinite_reverse]">
            <div className="absolute bottom-0 left-1/2 -translate-x-1/2 translate-y-6 w-20 h-20 bg-orange-500 rounded-full flex flex-col items-center justify-center shadow-lg text-white text-[10px] font-bold">
              <div>Option</div>
              <div className="text-lg">100萬</div>
            </div>
          </div>
        </div>

      </div>

    </div>
  );
}
