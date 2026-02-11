
import { Calendar, Database, Zap, Target, Banknote, Coins } from 'lucide-react';

export default function CafecaFintechSlide2() {
  return (
    <div className="h-full w-full bg-slate-50 relative flex overflow-hidden">

      {/* Info: (20260122 - Luphia) Left Sidebar - Summary */}
      <div className="w-[35%] h-full bg-white border-r border-slate-200 p-10 flex flex-col relative z-10 shadow-lg">
        <div className="flex-1">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-sky-100 text-sky-700 text-xs font-bold tracking-wider mb-8">
            <Zap size={14} />
            COMPANY PROFILE
          </div>

          <h2 className="text-4xl font-extrabold text-slate-900 leading-tight mb-8">
            <span className="text-sky-600">CAFECA</span>
            <br />
            Fintech
          </h2>

          <div className="space-y-8">
            <div className="flex items-start gap-4">
              <div className="bg-sky-50 p-3 rounded-xl shrink-0">
                <Calendar className="text-sky-600" size={24} />
              </div>
              <div>
                <div className="text-sm text-slate-500 font-medium uppercase tracking-wide mb-1">成立時間</div>
                <div className="text-xl font-bold text-slate-900 font-mono">2017.04.18</div>
              </div>
            </div>

            <div className="flex items-start gap-4">
              <div className="bg-sky-50 p-3 rounded-xl shrink-0">
                <Target className="text-sky-600" size={24} />
              </div>
              <div>
                <div className="text-sm text-slate-500 font-medium uppercase tracking-wide mb-1">核心聚焦</div>
                <div className="text-xl font-bold text-slate-900">金融科技技術</div>
              </div>
            </div>

            <div className="flex items-start gap-4">
              <div className="bg-sky-50 p-3 rounded-xl shrink-0">
                <Banknote className="text-sky-600" size={24} />
              </div>
              <div>
                <div className="text-sm text-slate-500 font-medium uppercase tracking-wide mb-1">額定股份</div>
                <div className="text-xl font-bold text-slate-900 font-mono">5,000,000 股</div>
              </div>
            </div>

            <div className="flex items-start gap-4">
              <div className="bg-sky-50 p-3 rounded-xl shrink-0">
                <Coins className="text-sky-600" size={24} />
              </div>
              <div>
                <div className="text-sm text-slate-500 font-medium uppercase tracking-wide mb-1">已發行股份</div>
                <div className="text-xl font-bold text-slate-900 font-mono">300,000 股</div>
              </div>
            </div>
          </div>
        </div>

        <div className="text-slate-400 text-[10px] font-mono border-t border-slate-100 pt-6">
          CAFECA FINTECH • PROPRIETARY & CONFIDENTIAL
        </div>
      </div>

      {/* Info: (20260122 - Luphia) Right Content - Compact Timeline */}
      <div className="flex-1 h-full bg-slate-50/50 p-10 flex flex-col justify-center relative overflow-hidden">
        <div className="absolute top-0 right-0 p-12 opacity-5 pointer-events-none">
          <Database size={400} />
        </div>

        <div className="space-y-5 relative z-10">

          {/* Info: (20260122 - Luphia) Timeline Item 1 */}
          <div className="flex gap-4 group">
            <div className="flex flex-col items-center">
              <div className="w-3 h-3 rounded-full bg-sky-500 mt-2 ring-4 ring-white shadow-sm"></div>
              <div className="w-0.5 flex-1 bg-slate-200 my-1 group-last:hidden"></div>
            </div>
            <div className="pb-2">
              <div className="flex items-baseline gap-3 mb-1">
                <span className="text-lg font-bold text-sky-600 font-mono">2018 - 2021</span>
                <h3 className="text-base font-bold text-slate-800">賦能好好證券</h3>
              </div>
              <p className="text-slate-600 text-sm leading-relaxed text-justify">
                加速金融監理沙盒實證流程，厚植創新金融業務之領照競爭力。
              </p>
            </div>
          </div>

          {/* Info: (20260122 - Luphia) Timeline Item 2 */}
          <div className="flex gap-4 group">
            <div className="flex flex-col items-center">
              <div className="w-3 h-3 rounded-full bg-sky-500 mt-2 ring-4 ring-white shadow-sm"></div>
              <div className="w-0.5 flex-1 bg-slate-200 my-1 group-last:hidden"></div>
            </div>
            <div className="pb-2">
              <div className="flex items-baseline gap-3 mb-1">
                <span className="text-lg font-bold text-sky-600 font-mono">2019</span>
                <h3 className="text-base font-bold text-slate-800">賦能金管會檢查局</h3>
              </div>
              <p className="text-slate-600 text-sm leading-relaxed text-justify">
                建構數位孿生實驗環境，強化金融政策之情境模擬與回測驗證。
              </p>
            </div>
          </div>

          {/* Info: (20260122 - Luphia) Timeline Item 3 */}
          <div className="flex gap-4 group">
            <div className="flex flex-col items-center">
              <div className="w-3 h-3 rounded-full bg-sky-500 mt-2 ring-4 ring-white shadow-sm"></div>
              <div className="w-0.5 flex-1 bg-slate-200 my-1 group-last:hidden"></div>
            </div>
            <div className="pb-2">
              <div className="flex items-baseline gap-3 mb-1">
                <span className="text-lg font-bold text-sky-600 font-mono">2020 - 2021</span>
                <h3 className="text-base font-bold text-slate-800">參與國家高速網路中心</h3>
              </div>
              <p className="text-slate-600 text-sm leading-relaxed text-justify">
                建構國家級 AI 大數據算力平台。
              </p>
            </div>
          </div>

          {/* Info: (20260122 - Luphia) Timeline Item 4 */}
          <div className="flex gap-4 group">
            <div className="flex flex-col items-center">
              <div className="w-3 h-3 rounded-full bg-orange-500 mt-2 ring-4 ring-white shadow-sm"></div>
            </div>
            <div>
              <div className="flex items-baseline gap-3 mb-1">
                <span className="text-lg font-bold text-orange-600 font-mono">2022 - Now</span>
                <h3 className="text-base font-bold text-slate-800">FAITH 核心引擎</h3>
              </div>
              <p className="text-slate-600 text-sm leading-relaxed text-justify">
                憑藉深厚的行業實務底蘊，開創以 <span className="font-bold text-orange-600">FAITH</span> (Financial Accounting Insight Trusted Heuristics) 為核心引擎的全方位金融科技生態，賦能多元化產品矩陣。
              </p>
            </div>
          </div>

        </div>
      </div>

    </div>
  );
}
