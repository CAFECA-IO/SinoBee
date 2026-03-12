import fs from 'fs/promises';
import path from 'path';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import Image from 'next/image';

export default async function Home() {
  const readmePath = path.join(process.cwd(), 'README.md');
  const readmeContent = await fs.readFile(readmePath, 'utf8');

  return (
    <main className="flex min-h-screen flex-col items-center justify-between p-4 md:p-12 lg:p-24 bg-gradient-to-br from-gray-900 via-gray-800 to-black">
      <div className="w-full max-w-5xl bg-white/5 p-6 md:p-10 rounded-2xl shadow-2xl border border-white/10 backdrop-blur-sm">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h1: ({ children, ...props }) => <h1 className="text-4xl md:text-5xl font-extrabold mb-8 text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-emerald-400 leading-tight" {...props}>{children}</h1>,
            h2: ({ children, ...props }) => <h2 className="text-2xl md:text-3xl font-bold mt-12 mb-6 border-b border-white/10 pb-4 text-gray-100" {...props}>{children}</h2>,
            h3: ({ children, ...props }) => <h3 className="text-xl md:text-2xl font-semibold mt-8 mb-4 text-gray-200" {...props}>{children}</h3>,
            p: ({ children, ...props }) => <p className="mb-6 text-gray-300 leading-relaxed text-lg" {...props}>{children}</p>,
            ul: ({ children, ...props }) => <ul className="list-disc list-outside ml-6 mb-6 space-y-2 text-gray-300 text-lg" {...props}>{children}</ul>,
            ol: ({ children, ...props }) => <ol className="list-decimal list-outside ml-6 mb-6 space-y-2 text-gray-300 text-lg" {...props}>{children}</ol>,
            li: ({ children, ...props }) => <li className="pl-2" {...props}>{children}</li>,
            a: ({ children, ...props }) => <a className="text-blue-400 hover:text-blue-300 transition-colors duration-200 underline decoration-blue-400/30 hover:decoration-blue-400" {...props}>{children}</a>,
            strong: ({ children, ...props }) => <strong className="font-semibold text-gray-100" {...props}>{children}</strong>,
            code: ({ className, children, ...props }) => {
              const match = /language-(\w+)/.exec(className || '');
              return match ? (
                <div className="bg-black/60 p-5 rounded-xl overflow-x-auto mb-6 border border-white/5 shadow-inner">
                  <code className={className} {...props}>
                    {children}
                  </code>
                </div>
              ) : (
                <code className="bg-black/40 text-emerald-300 px-2 py-0.5 rounded text-sm whitespace-nowrap border border-white/5" {...props}>
                  {children}
                </code>
              )
            },
            blockquote: ({ children, ...props }) => <blockquote className="border-l-4 border-emerald-500/50 bg-emerald-500/5 pl-6 pr-4 py-4 rounded-r-lg italic my-6 text-gray-400" {...props}>{children}</blockquote>,
            table: ({ children, ...props }) => (
              <div className="overflow-x-auto mb-8 rounded-xl border border-white/10">
                <table className="min-w-full border-collapse text-left text-sm" {...props}>{children}</table>
              </div>
            ),
            thead: ({ children, ...props }) => <thead className="bg-white/5 border-b border-white/10" {...props}>{children}</thead>,
            th: ({ children, ...props }) => <th className="px-6 py-4 font-semibold text-gray-200" {...props}>{children}</th>,
            td: ({ children, ...props }) => <td className="px-6 py-4 border-b border-white/5 text-gray-300" {...props}>{children}</td>,
            img: ({ alt, src }: React.ImgHTMLAttributes<HTMLImageElement>) => {
              if (typeof src !== 'string') return null;
              const imageSrc = src.startsWith('public/') ? src.replace('public/', '/') : src;
              return (
                <Image
                  src={imageSrc}
                  alt={alt || ''}
                  width={800}
                  height={400}
                  className="rounded-xl border border-white/10 shadow-lg max-w-full h-auto my-8"
                />
              );
            }
          }}
        >
          {readmeContent}
        </ReactMarkdown>
      </div>
    </main>
  );
}
