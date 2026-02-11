export default function CafecaFintechSlide2() {
  return (
    <div className="min-h-screen w-full bg-slate-100 flex items-center justify-center p-4">
      <div className="w-[1280px] h-[720px] bg-white relative overflow-hidden shadow-2xl flex flex-col border border-gray-200">

        {/* Info: (20260123) Background Gradients - Keeping consistent with Slide 1 but slightly varied */}
        <div
          className="absolute inset-x-0 -top-40 -z-10 transform-gpu overflow-hidden blur-3xl pointer-events-none"
          aria-hidden="true"
        >
          <div
            className="relative left-[calc(50%-11rem)] aspect-[1155/678] w-[70rem] -translate-x-1/2 rotate-[30deg] bg-gradient-to-tr from-[#0ea5e9] to-indigo-400 opacity-20"
            style={{
              clipPath:
                'polygon(74.1% 44.1%, 100% 61.6%, 97.5% 26.9%, 85.5% 0.1%, 80.7% 2%, 72.5% 32.5%, 60.2% 62.4%, 52.4% 68.1%, 47.5% 58.3%, 45.2% 34.5%, 27.5% 76.7%, 0.1% 64.9%, 17.9% 100%, 27.6% 76.8%, 76.1% 97.7%, 74.1% 44.1%)',
            }}
          />
        </div>

        {/* Info: (20260123) Header Area */}
        <div className="px-16 pt-12">
          <div className="flex items-center gap-4 mb-6">
            <div className="w-12 h-1 bg-sky-500 rounded-full"></div>
            <span className="text-sky-600 font-bold tracking-[0.2em] uppercase text-sm">企業願景</span>
          </div>
          <h1 className="text-5xl font-extrabold text-slate-800 leading-tight">
            打造 <span className="text-sky-600">MIT</span> 銀行核心系統
            <br />
            <span className="text-3xl text-slate-400 font-light mt-2 block">對標全球標準</span>
          </h1>
        </div>

        {/* Info: (20260123) Main Content - 3 Pillars */}
        <div className="flex-1 px-16 flex items-center justify-between gap-8 h-full">

          {/* Info: (20260123) Card 1: Benchmark */}
          <div className="flex-1 h-80 bg-slate-50 rounded-2xl p-8 border border-slate-100 shadow-sm relative overflow-hidden group hover:shadow-md transition-all">
            <div className="absolute top-0 right-0 w-32 h-32 bg-sky-100 rounded-bl-[100px] -mr-8 -mt-8 opacity-50"></div>

            <h3 className="text-xl font-bold text-slate-700 mb-4 z-10 relative">對標目標</h3>
            <div className="text-4xl font-extrabold text-blue-900 mb-6 bg-clip-text text-transparent bg-gradient-to-r from-blue-900 to-blue-700">
              Temenos
            </div>
            <p className="text-slate-500 leading-relaxed font-light text-justify">
              對標全球第一銀行軟體 Temenos，確保企業級可靠性、擴展性與安全架構。
            </p>
            <div className="absolute bottom-8 right-8 text-sky-200">
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
              </svg>
            </div>
          </div>

          {/* Info: (20260123) Card 2: MIT Core */}
          <div className="flex-1 h-96 bg-gradient-to-br from-sky-600 to-blue-700 rounded-2xl p-8 shadow-xl text-white relative overflow-hidden transform scale-105 z-10">
            <div className="absolute inset-0 bg-[url('https://www.transparenttextures.com/patterns/cubes.png')] opacity-10"></div>

            <div className="flex items-center gap-3 mb-6">
              <span className="bg-white/20 px-3 py-1 rounded-full text-xs font-bold tracking-widest backdrop-blur-sm">核心使命</span>
            </div>

            <h2 className="text-4xl font-bold mb-6">
              MIT <br />
              <span className="text-2xl font-normal opacity-90">銀行系統</span>
            </h2>

            <p className="text-blue-100 leading-relaxed text-lg mb-8">
              臺灣製造。 <br />
              自主研發的高效能金融基礎設施，專為完全自主與快速在地化而設計。
            </p>

            <div className="w-full bg-white/10 h-1.5 rounded-full overflow-hidden">
              <div className="w-3/4 h-full bg-yellow-400 shadow-[0_0_10px_rgba(250,204,21,0.5)]"></div>
            </div>
          </div>

          {/* Info: (20260123) Card 3: Global Market */}
          <div className="flex-1 h-80 bg-slate-50 rounded-2xl p-8 border border-slate-100 shadow-sm relative overflow-hidden group hover:shadow-md transition-all">
            <div className="absolute top-0 right-0 w-32 h-32 bg-indigo-100 rounded-bl-[100px] -mr-8 -mt-8 opacity-50"></div>

            <h3 className="text-xl font-bold text-slate-700 mb-4 z-10 relative">全球雄心</h3>
            <div className="text-3xl font-extrabold text-indigo-900 mb-6">
              挑戰全球市場
            </div>
            <p className="text-slate-500 leading-relaxed font-light text-justify">
              以具競爭力、模組化且現代化的技術棧挑戰國際市場，隨時準備跨境部署。
            </p>
            <div className="absolute bottom-8 right-8 text-indigo-200">
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="2" y1="12" x2="22" y2="12"></line>
                <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
              </svg>
            </div>
          </div>

        </div>

        {/* Info: (20260123) Footer */}
        <div className="absolute bottom-4 w-full px-16 flex justify-between text-gray-400 text-xs tracking-widest uppercase font-medium">
          <div>企業戰略</div>
          <div className="flex gap-2 items-center">
            <div className="w-2 h-2 rounded-full bg-sky-400"></div>
            2026 CAFECA FINTECH
          </div>
        </div>

      </div>
    </div>
  );
}
