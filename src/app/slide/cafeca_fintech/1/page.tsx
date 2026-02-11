export default function CafecaFintechSlide1() {
  return (
    <div className="min-h-screen w-full bg-slate-100 flex items-center justify-center p-4">
      <div className="w-[1280px] h-[720px] bg-white relative overflow-hidden shadow-2xl flex flex-col items-center justify-center border border-gray-200">

        {/* Info: (20260122 - Luphia) Background Gradients */}
        <div
          className="absolute inset-x-0 -top-40 -z-10 transform-gpu overflow-hidden blur-3xl pointer-events-none"
          aria-hidden="true"
        >
          <div
            className="relative left-[calc(50%-11rem)] aspect-[1155/678] w-[70rem] -translate-x-1/2 rotate-[30deg] bg-gradient-to-tr from-[#0ea5e9] to-[#38bdf8] opacity-30"
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
            className="relative left-[calc(50%+3rem)] aspect-[1155/678] w-[70rem] -translate-x-1/2 bg-gradient-to-tr from-[#0284c7] to-[#0ea5e9] opacity-30"
            style={{
              clipPath:
                'polygon(74.1% 44.1%, 100% 61.6%, 97.5% 26.9%, 85.5% 0.1%, 80.7% 2%, 72.5% 32.5%, 60.2% 62.4%, 52.4% 68.1%, 47.5% 58.3%, 45.2% 34.5%, 27.5% 76.7%, 0.1% 64.9%, 17.9% 100%, 27.6% 76.8%, 76.1% 97.7%, 74.1% 44.1%)',
            }}
          />
        </div>

        {/* Info: (20260122 - Luphia) Content Container */}
        <div className="z-10 text-center space-y-6">

          {/* Info: (20260122 - Luphia) Brand area */}
          <div className="mb-12">
            <span className="text-sky-600 font-bold tracking-[0.2em] text-sm md:text-base border border-sky-200 px-4 py-1 rounded-full uppercase bg-sky-50">
              CAFECA Fintech
            </span>
          </div>

          {/* Info: (20260122 - Luphia) Main Title */}
          <h1 className="text-6xl md:text-7xl font-extrabold tracking-tight text-transparent bg-clip-text bg-gradient-to-br from-sky-700 via-sky-500 to-blue-400 leading-tight drop-shadow-sm pb-2">
            CAFECA Fintech
            <br />
            <span className="text-5xl md:text-5xl text-gray-500 font-light mt-4 block">
              Innovating Future Finance
            </span>
          </h1>

          {/* Info: (20260122 - Luphia) Subtitle */}
          <p className="text-2xl text-gray-600 font-light tracking-wide mt-6 border-t border-sky-100 pt-8 inline-block">
            Next Generation Financial Solutions
          </p>

        </div>

        {/* Info: (20260122 - Luphia) Footer / Deco */}
        <div className="absolute bottom-4 w-full px-16 flex justify-between text-gray-400 text-xs tracking-widest uppercase font-medium">
          <div>Confidential</div>
          <div className="flex gap-2 items-center">
            <div className="w-2 h-2 rounded-full bg-sky-400"></div>
            2026 CAFECA FINTECH
          </div>
        </div>

      </div>
    </div>
  );
}
