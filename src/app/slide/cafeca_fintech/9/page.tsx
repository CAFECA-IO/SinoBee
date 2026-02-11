import { Landmark, FileKey, BarChart3, Scale, KeyRound, FileDigit, BadgeCheck } from 'lucide-react';

export default function CafecaFintechSlide6() {
  return (
    <div className="h-full w-full bg-slate-50 relative flex overflow-hidden">

      {/* Info: (20260122 - Luphia) Left Sidebar - Main Theme */}
      <div className="w-[32%] h-full bg-slate-900 text-white p-10 flex flex-col justify-between relative z-10 shadow-2xl">

        <div className="absolute top-0 right-0 w-[300px] h-[300px] bg-sky-600/20 rounded-full blur-[80px] -translate-y-1/2 translate-x-1/3"></div>

        <div className="relative z-10">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-sky-900/50 border border-sky-700/50 text-sky-400 text-xs font-bold tracking-wider mb-8">
            <Scale size={14} />
            REGULATORY SANDBOX
          </div>

          <h2 className="text-5xl font-extrabold leading-tight mb-6">
            <span className="text-sky-400">金融監理</span>
            <br />
            沙盒實驗
          </h2>

          <div className="p-6 bg-slate-800/50 rounded-2xl border border-slate-700/50 backdrop-blur-sm mt-8">
            <div className="flex items-center gap-3 mb-3 text-sky-400 font-bold text-lg">
              <Landmark size={24} />
              合作邀請
            </div>
            <p className="text-slate-300 text-sm leading-relaxed text-justify mb-4">
              依據《金融科技發展與創新實驗條例》，我們正積極籌備申請監理沙盒。
            </p>
            <p className="text-slate-400 text-xs leading-relaxed border-t border-slate-700 pt-4">
              若貴機構持有 <span className="text-white font-bold">證券商</span>、<span className="text-white font-bold">商業銀行</span> 或 <span className="text-white font-bold">信託業</span> 牌照，我們誠摯邀請您共同擔任「創新實驗申請人」。
            </p>
          </div>
        </div>

        <div className="text-[10px] text-slate-500 font-mono relative z-10">
          FINANCIAL REGULATORY SANDBOX EXPERIMENT
        </div>
      </div>

      {/* Info: (20260122 - Luphia) Right Content - Grid Layout */}
      <div className="flex-1 h-full p-12 bg-slate-100/50 overflow-y-auto">
        <div className="grid grid-cols-2 gap-6 h-full">

          {/* Info: (20260122 - Luphia) Card 1: Convertible Bond */}
          <div className="col-span-2 bg-white p-8 rounded-3xl shadow-sm hover:shadow-lg transition-all border border-slate-200 flex gap-8 items-center group">
            <div className="w-20 h-20 bg-sky-50 rounded-2xl flex items-center justify-center text-sky-600 shrink-0 group-hover:scale-105 transition-transform">
              <FileKey size={40} />
            </div>
            <div>
              <h3 className="text-xl font-bold text-slate-900 mb-2 group-hover:text-sky-600 transition-colors">
                Convertible Bond 可轉換公司債發行與交換平臺
              </h3>
              <p className="text-slate-600 text-sm leading-relaxed">
                打造高效、透明的可轉換公司債發行與流通體系，透過區塊鏈技術確保資產權屬清晰與交易即時結算。
              </p>
            </div>
          </div>

          {/* Info: (20260122 - Luphia) Card 2: FIDO2 */}
          <div className="bg-white p-6 rounded-3xl shadow-sm hover:shadow-lg transition-all border border-slate-200 group">
            <div className="w-12 h-12 bg-emerald-50 rounded-xl flex items-center justify-center text-emerald-600 mb-4 group-hover:scale-110 transition-transform">
              <KeyRound size={24} />
            </div>
            <h3 className="text-lg font-bold text-slate-900 mb-2">FIDO2 數位身分證</h3>
            <div className="text-xs font-bold text-slate-400 mb-3 uppercase tracking-wider">Digital Identity</div>
            <p className="text-slate-600 text-xs leading-relaxed text-justify">
              導入 FIDO2 國際級無密碼認證標準，結合數位簽章技術，確保每一筆金融交易與資產轉移皆在最高等級的安全框架下執行。
            </p>
          </div>

          {/* Info: (20260122 - Luphia) Card 3: Intelligent Audit */}
          <div className="bg-white p-6 rounded-3xl shadow-sm hover:shadow-lg transition-all border border-slate-200 group">
            <div className="w-12 h-12 bg-orange-50 rounded-xl flex items-center justify-center text-orange-600 mb-4 group-hover:scale-110 transition-transform">
              <FileDigit size={24} />
            </div>
            <h3 className="text-lg font-bold text-slate-900 mb-2">智能會計審計與核銷</h3>
            <div className="text-xs font-bold text-slate-400 mb-3 uppercase tracking-wider">Intelligent Audit</div>
            <p className="text-slate-600 text-xs leading-relaxed text-justify">
              運用人工智能會計審計技術，即時追蹤並自動化核銷企業之專案執行進度與資金用途，大幅提升投後管理效率，防範資金濫用。
            </p>
          </div>

          {/* Info: (20260122 - Luphia) Card 4: Financial Product Rating (Full Width Bottom) */}
          <div className="col-span-2 bg-gradient-to-r from-slate-900 to-slate-800 p-8 rounded-3xl shadow-lg border border-slate-700 flex gap-8 items-center text-white relative overflow-hidden group">
            <div className="absolute top-0 right-0 w-64 h-64 bg-sky-500/10 rounded-full blur-3xl -translate-y-1/2 translate-x-1/2"></div>

            <div className="w-16 h-16 bg-white/10 rounded-2xl flex items-center justify-center text-sky-400 shrink-0 backdrop-blur-sm group-hover:rotate-12 transition-transform">
              <BarChart3 size={32} />
            </div>
            <div className="relative z-10 flex-1">
              <div className="flex justify-between items-start">
                <div>
                  <h3 className="text-xl font-bold mb-1">金融商品評級技術</h3>
                  <div className="text-xs font-bold text-slate-400 mb-3 uppercase tracking-wider">Financial Product Rating</div>
                </div>
                <BadgeCheck className="text-sky-400" size={24} />
              </div>
              <p className="text-slate-300 text-sm leading-relaxed">
                動態整合數位足跡與財報邏輯，透過 AI 評級模型對金融商品進行即時風險量化，提供最具公信力的定價參考。
              </p>
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}
