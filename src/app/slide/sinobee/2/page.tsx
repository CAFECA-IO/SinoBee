'use client';

import Image from 'next/image';
import { Database, TrendingDown, Settings } from 'lucide-react';

export default function SinoBeeSlide2() {
  return (
    <div className="min-h-screen w-full bg-[#0B0F19] flex items-center justify-center p-4">
      <div className="w-[1280px] h-[720px] bg-[#020617] relative overflow-hidden shadow-2xl flex flex-col border border-slate-800">

        {/* Info: (20260209 - Luphia) Background Gradients for SinoBee (Dark Blue/Gold/Red theme) */}
        <div
          className="absolute inset-x-0 -top-40 -z-10 transform-gpu overflow-hidden blur-3xl pointer-events-none"
          aria-hidden="true"
        >
          <div
            className="relative left-[calc(50%-11rem)] aspect-[1155/678] w-[70rem] -translate-x-1/2 rotate-[30deg] bg-gradient-to-tr from-[#991b1b] to-[#b45309] opacity-20"
            style={{
              clipPath:
                'polygon(74.1% 44.1%, 100% 61.6%, 97.5% 26.9%, 85.5% 0.1%, 80.7% 2%, 72.5% 32.5%, 60.2% 62.4%, 52.4% 68.1%, 47.5% 58.3%, 45.2% 34.5%, 27.5% 76.7%, 0.1% 64.9%, 17.9% 100%, 27.6% 76.8%, 76.1% 97.7%, 74.1% 44.1%)',
            }}
          />
        </div>

        <div
          className="absolute inset-x-0 top-[calc(100%-13rem)] -z-10 transform-gpu overflow-hidden blur-3xl pointer-events-none"
          aria-hidden="true"
        >
          <div
            className="relative left-[calc(50%+3rem)] aspect-[1155/678] w-[70rem] -translate-x-1/2 bg-gradient-to-tr from-[#78350f] to-[#f59e0b] opacity-20"
            style={{
              clipPath:
                'polygon(74.1% 44.1%, 100% 61.6%, 97.5% 26.9%, 85.5% 0.1%, 80.7% 2%, 72.5% 32.5%, 60.2% 62.4%, 52.4% 68.1%, 47.5% 58.3%, 45.2% 34.5%, 27.5% 76.7%, 0.1% 64.9%, 17.9% 100%, 27.6% 76.8%, 76.1% 97.7%, 74.1% 44.1%)',
            }}
          />
        </div>

        {/* Info: (20260209 - Luphia) Header */}
        <div className="pt-12 px-16 flex justify-between items-start z-10">
          <div>
            <h2 className="text-5xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-amber-200 to-amber-500 drop-shadow-sm">
              SinoBee 想解決的問題
            </h2>
            <div className="h-1 w-32 bg-gradient-to-r from-amber-500 to-amber-900 mt-4 rounded-full"></div>
          </div>

          {/* Info: (20260209 - Luphia) Logo Watermark */}
          <div className="opacity-50 grayscale hover:grayscale-0 transition-all duration-500">
            <Image
              src="/sinopac.logo.svg"
              alt="SinoPac Securities"
              width={150}
              height={45}
              className="h-8 w-auto"
            />
          </div>
        </div>

        {/* Info: (20260209 - Luphia) Content Cards */}
        <div className="flex-1 px-16 flex items-center justify-between gap-8 z-10">

          {/* Info: (20260209 - Luphia) Card 1 */}
          <div className="group relative flex-1 h-[420px] bg-gradient-to-b from-slate-900/80 to-slate-950/80 border border-slate-800 rounded-2xl p-8 backdrop-blur-md hover:border-amber-500/50 transition-all duration-300 hover:-translate-y-2 hover:shadow-[0_20px_40px_-15px_rgba(245,158,11,0.15)]">
            <div className="w-14 h-14 bg-gradient-to-br from-slate-800 to-slate-900 rounded-xl flex items-center justify-center mb-6 shadow-lg group-hover:scale-110 transition-transform duration-300 ring-1 ring-slate-700/50 group-hover:ring-amber-500/30">
              <Database className="text-amber-500" size={28} />
            </div>
            <h3 className="text-2xl font-bold text-slate-100 mb-2">
              數據碎片化與<br />決策盲區
            </h3>
            <p className="text-amber-500/80 text-sm font-semibold mb-4 tracking-wider uppercase">Data Fragmentation</p>
            <p className="text-slate-400 leading-relaxed font-light">
              跨系統整合斷層，可能造成<span className="text-slate-200 font-medium">精準行銷失焦</span>，風險預警滯後，難以在黃金時刻採取行動，<span className="text-red-400/90 font-medium">錯失變現良機</span>。
            </p>
            <div className="absolute inset-0 border-2 border-transparent group-hover:border-amber-500/20 rounded-2xl pointer-events-none transition-colors duration-300"></div>
          </div>

          {/* Info: (20260209 - Luphia) Card 2 */}
          <div className="group relative flex-1 h-[420px] bg-gradient-to-b from-slate-900/80 to-slate-950/80 border border-slate-800 rounded-2xl p-8 backdrop-blur-md hover:border-amber-500/50 transition-all duration-300 hover:-translate-y-2 hover:shadow-[0_20px_40px_-15px_rgba(245,158,11,0.15)]">
            <div className="w-14 h-14 bg-gradient-to-br from-slate-800 to-slate-900 rounded-xl flex items-center justify-center mb-6 shadow-lg group-hover:scale-110 transition-transform duration-300 ring-1 ring-slate-700/50 group-hover:ring-amber-500/30">
              <TrendingDown className="text-amber-500" size={28} />
            </div>
            <h3 className="text-2xl font-bold text-slate-100 mb-2">
              獲客成本與<br />價值失衡
            </h3>
            <p className="text-amber-500/80 text-sm font-semibold mb-4 tracking-wider uppercase">Cost vs. Value</p>
            <p className="text-slate-400 leading-relaxed font-light">
              證券市場紅海競爭推升獲客成本，<span className="text-slate-200 font-medium">缺乏留存與深耕機制</span>，導致<span className="text-red-400/90 font-medium">營收難以覆蓋成本</span>，長期價值無法體現。
            </p>
            <div className="absolute inset-0 border-2 border-transparent group-hover:border-amber-500/20 rounded-2xl pointer-events-none transition-colors duration-300"></div>
          </div>

          {/* Info: (20260209 - Luphia) Card 3 */}
          <div className="group relative flex-1 h-[420px] bg-gradient-to-b from-slate-900/80 to-slate-950/80 border border-slate-800 rounded-2xl p-8 backdrop-blur-md hover:border-amber-500/50 transition-all duration-300 hover:-translate-y-2 hover:shadow-[0_20px_40px_-15px_rgba(245,158,11,0.15)]">
            <div className="w-14 h-14 bg-gradient-to-br from-slate-800 to-slate-900 rounded-xl flex items-center justify-center mb-6 shadow-lg group-hover:scale-110 transition-transform duration-300 ring-1 ring-slate-700/50 group-hover:ring-amber-500/30">
              <Settings className="text-amber-500" size={28} />
            </div>
            <h3 className="text-2xl font-bold text-slate-100 mb-2">
              營運摩擦與<br />規模化瓶頸
            </h3>
            <p className="text-amber-500/80 text-sm font-semibold mb-4 tracking-wider uppercase">Operational Friction</p>
            <p className="text-slate-400 leading-relaxed font-light">
              流程<span className="text-slate-200 font-medium">自動化不足</span>，作業風險隨業務量倍增，且高價值人才陷入低階維運泥淖，<span className="text-red-400/90 font-medium">阻礙業務創新</span>。
            </p>
            <div className="absolute inset-0 border-2 border-transparent group-hover:border-amber-500/20 rounded-2xl pointer-events-none transition-colors duration-300"></div>
          </div>

        </div>


        {/* Info: (20260209 - Luphia) Footer */}
        <div className="absolute bottom-8 w-full text-center">
          <p className="text-slate-500 text-sm tracking-[0.2em] uppercase font-medium">
            Confidential • <span className="font-bold text-slate-400">CAFECA Fintech</span>
          </p>
        </div>

      </div>
    </div>
  );
}
