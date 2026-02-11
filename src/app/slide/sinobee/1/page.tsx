import Image from 'next/image';

export default function SinoBeeSlide1() {
  return (
    <div className="min-h-screen w-full bg-[#0B0F19] flex items-center justify-center p-4">
      <div className="w-[1280px] h-[720px] bg-[#020617] relative overflow-hidden shadow-2xl flex flex-col items-center justify-center border border-slate-800">

        {/* Info: (20260209 - Luphia) SinoPac Logo (Top-Left) */}
        <div className="absolute top-8 left-8 z-20 opacity-90">
          <Image
            src="/sinopac.logo.svg"
            alt="SinoPac Securities"
            width={200}
            height={60}
            className="h-12 w-auto"
          />
        </div>

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

        {/* Info: (20260209 - Luphia) Content Container */}
        <div className="z-10 text-center space-y-8">

          {/* Info: (20260209 - Luphia) Brand area */}
          <div className="mb-8">
            {/* Info: (20260209 - Luphia) Gold Circle */}

            <div className="mb-6 flex justify-center">
              <Image
                src="/logo.svg"
                alt="SinoBee Logo"
                width={120}
                height={120}
                className="w-32 h-32 drop-shadow-[0_0_20px_rgba(245,158,11,0.5)]"
              />
            </div>
          </div>

          {/* Info: (20260209 - Luphia) Main Title */}
          <h1 className="text-7xl font-extrabold tracking-tight text-white leading-tight drop-shadow-[0_2px_10px_rgba(0,0,0,0.5)] pb-4">
            <span className="bg-clip-text text-transparent bg-gradient-to-r from-amber-200 via-yellow-400 to-amber-500">SinoBee</span>
            <span className="text-slate-100 ml-4 font-bold">小蜜豐</span>
          </h1>

          {/* Info: (20260209 - Luphia) Subtitle */}
          <div className="flex items-center justify-center gap-4 mt-6">
            <div className="h-px w-16 bg-gradient-to-r from-transparent to-amber-500/50"></div>
            <p className="text-3xl text-slate-300 font-light tracking-wide max-w-3xl leading-relaxed">
              24 小時為你採蜜的財富釀金師
            </p>
            <div className="h-px w-16 bg-gradient-to-l from-transparent to-amber-500/50"></div>
          </div>

        </div>

        {/* Info: (20260209 - Luphia) Footer / Powered By */}
        <div className="absolute bottom-8 w-full text-center">
          <p className="text-slate-500 text-sm tracking-[0.2em] uppercase font-medium">
            powered by <span className="font-bold text-slate-400">CAFECA Fintech</span>
          </p>
        </div>

      </div>
    </div>
  );
}
