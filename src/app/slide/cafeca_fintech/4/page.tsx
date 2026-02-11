import { Fingerprint, Lock, Server, Brain, Zap } from 'lucide-react';

export default function CafecaFintechSlide3() {
  return (
    <div className="h-full w-full bg-slate-50 relative flex overflow-hidden">

      {/* Info: (20260122 - Luphia) Left Sidebar - Title and Decoration */}
      <div className="w-[30%] h-full bg-slate-900 text-white p-10 flex flex-col justify-between relative shadow-2xl z-20">
        <div>
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-sky-900/50 border border-sky-700/50 text-sky-400 text-xs font-bold tracking-wider mb-8">
            <Zap size={14} />
            CORE TECHNOLOGY
          </div>

          <h2 className="text-5xl font-extrabold leading-tight mb-6">
            <span className="text-sky-400">核心</span>
            <br />
            技術
          </h2>
          <p className="text-slate-400 leading-relaxed text-lg">
            四大核心支柱，構建安全、高效、智能的金融科技生態系統。
          </p>
        </div>

        {/* Info: (20260122 - Luphia) Abstract graphic */}
        <div className="relative w-full aspect-square border-t border-slate-700 pt-8 opacity-50">
          <div className="absolute inset-0 bg-gradient-to-br from-sky-500/20 to-transparent rounded-full blur-3xl"></div>
          <div className="grid grid-cols-2 gap-4 h-full">
            <div className="bg-slate-800/50 rounded-2xl"></div>
            <div className="bg-slate-800/30 rounded-2xl translate-y-8"></div>
            <div className="bg-slate-800/30 rounded-2xl -translate-y-4"></div>
            <div className="bg-slate-800/50 rounded-2xl"></div>
          </div>
        </div>
      </div>

      {/* Info: (20260122 - Luphia) Right Content - 2x2 Grid */}
      <div className="flex-1 h-full p-12 bg-slate-100 flex items-center justify-center">
        <div className="grid grid-cols-2 gap-8 w-full max-w-5xl h-full max-h-[600px]">

          {/* Info: (20260122 - Luphia) Card 1: Digital ID */}
          <div className="bg-white p-6 rounded-2xl shadow-sm hover:shadow-xl transition-all duration-300 group border border-slate-200 flex flex-col">
            <div className="flex items-center gap-4 mb-4">
              <div className="w-12 h-12 bg-sky-50 rounded-xl flex items-center justify-center text-sky-600 group-hover:scale-110 transition-transform duration-300 shrink-0">
                <Fingerprint size={24} />
              </div>
              <h3 className="text-lg font-bold text-slate-900 leading-tight">CAFECA 數位身份證</h3>
            </div>
            <p className="text-slate-600 text-xs leading-relaxed text-justify mb-4 flex-1">
              利用生物識別和先進加密技術，我們創建不可篡改的數位 ID 和電子簽章系統。
            </p>
            <div className="bg-sky-50/50 rounded-lg p-3 border border-sky-100/50">
              <span className="inline-block px-1.5 py-0.5 rounded-md bg-sky-100 text-sky-700 text-[9px] font-bold uppercase tracking-wider mb-1.5">The Keystone</span>
              <p className="text-[11px] text-slate-600 font-medium leading-snug">協助歐生全開發冷錢包交換數位身分證技術</p>
            </div>
          </div>

          {/* Info: (20260122 - Luphia) Card 2: Alohomora */}
          <div className="bg-white p-6 rounded-2xl shadow-sm hover:shadow-xl transition-all duration-300 group border border-slate-200 flex flex-col">
            <div className="flex items-center gap-4 mb-4">
              <div className="w-12 h-12 bg-purple-50 rounded-xl flex items-center justify-center text-purple-600 group-hover:scale-110 transition-transform duration-300 shrink-0">
                <Lock size={24} />
              </div>
              <h3 className="text-lg font-bold text-slate-900 leading-tight">Alohomora 同態加密</h3>
            </div>
            <p className="text-slate-600 text-xs leading-relaxed text-justify mb-4 flex-1">
              透過特殊的代數結構設計，使資訊在加密狀態下仍能保有數學特性，實現數據可用不可見的高強度安全計算。
            </p>
            <div className="bg-purple-50/50 rounded-lg p-3 border border-purple-100/50">
              <span className="inline-block px-1.5 py-0.5 rounded-md bg-purple-100 text-purple-700 text-[9px] font-bold uppercase tracking-wider mb-1.5">Why we did this</span>
              <p className="text-[11px] text-slate-600 font-medium leading-snug">讓金管會即時同步好好證券數據</p>
            </div>
          </div>

          {/* Info: (20260122 - Luphia) Card 3: Locutus */}
          <div className="bg-white p-6 rounded-2xl shadow-sm hover:shadow-xl transition-all duration-300 group border border-slate-200 flex flex-col">
            <div className="flex items-center gap-4 mb-4">
              <div className="w-12 h-12 bg-emerald-50 rounded-xl flex items-center justify-center text-emerald-600 group-hover:scale-110 transition-transform duration-300 shrink-0">
                <Server size={24} />
              </div>
              <h3 className="text-lg font-bold text-slate-900 leading-tight">Locutus 邊緣運算</h3>
            </div>
            <p className="text-slate-600 text-xs leading-relaxed text-justify mb-4 flex-1">
              利用邊緣運算技術整合全球分佈式閒置節點，構建出具備極致彈性與超大規模的去中心化算力矩陣。
            </p>
            <div className="bg-emerald-50/50 rounded-lg p-3 border border-emerald-100/50">
              <span className="inline-block px-1.5 py-0.5 rounded-md bg-emerald-100 text-emerald-700 text-[9px] font-bold uppercase tracking-wider mb-1.5">Fire while walking</span>
              <p className="text-[11px] text-slate-600 font-medium leading-snug">打造 DeFi 基礎設施</p>
            </div>
          </div>

          {/* Info: (20260122 - Luphia) Card 4: FAITH */}
          <div className="bg-white p-6 rounded-2xl shadow-sm hover:shadow-xl transition-all duration-300 group border border-slate-200 flex flex-col">
            <div className="flex items-center gap-4 mb-4">
              <div className="w-12 h-12 bg-orange-50 rounded-xl flex items-center justify-center text-orange-600 group-hover:scale-110 transition-transform duration-300 shrink-0">
                <Brain size={24} />
              </div>
              <h3 className="text-lg font-bold text-slate-900 leading-tight">FAITH 智能模型</h3>
            </div>
            <p className="text-slate-600 text-xs leading-relaxed text-justify mb-4 flex-1">
              會計專業多模態模型，驅動 iSunFA 等多元化的 AI 自動化服務，賦能企業實現智慧會計轉型。
            </p>
            <div className="bg-orange-50/50 rounded-lg p-3 border border-orange-100/50">
              <span className="inline-block px-1.5 py-0.5 rounded-md bg-orange-100 text-orange-700 text-[9px] font-bold uppercase tracking-wider mb-1.5">How we did this</span>
              <p className="text-[11px] text-slate-600 font-medium leading-snug">參與國家高速網路中心計畫換取 AI 訓練算力</p>
            </div>
          </div>
        </div>
      </div>

      {/* Info: (20260122 - Luphia) Footer for Right Side */}
      <div className="absolute bottom-6 right-12 text-slate-300 text-[10px] font-mono mix-blend-multiply">
        CAFECA FINTECH • CORE TECHNOLOGIES
      </div>

    </div>
  );
}
