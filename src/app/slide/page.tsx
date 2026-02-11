import fs from 'fs/promises';
import path from 'path';
import Link from 'next/link';
import { Folder, ChevronRight } from 'lucide-react';

export default async function SlideIndexPage() {
  const slidesDir = path.join(process.cwd(), 'src/app/slide');
  let slideDecks: string[] = [];

  try {
    const entries = await fs.readdir(slidesDir, { withFileTypes: true });

    slideDecks = entries
      .filter(entry =>
        entry.isDirectory() &&
        !entry.name.startsWith('.') &&
        !['print', 'api'].includes(entry.name)
      )
      .map(entry => entry.name);
  } catch (error) {
    console.error('Error reading slides directory:', error);
  }

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <div className="max-w-5xl mx-auto">
        <div className="mb-10">
          <h1 className="text-3xl font-bold text-gray-900">Presentation Decks</h1>
          <p className="text-gray-500 mt-2">Select a slide deck to view</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {slideDecks.map((deck) => (
            <Link
              key={deck}
              href={`/slide/${deck}`}
              className="group block h-full"
            >
              <div className="bg-white rounded-2xl p-6 shadow-sm border border-gray-100 hover:shadow-md hover:border-orange-200 transition-all duration-300 h-full flex flex-col">
                <div className="flex items-start justify-between mb-4">
                  <div className="w-12 h-12 bg-orange-50 rounded-xl flex items-center justify-center text-orange-600 group-hover:bg-orange-600 group-hover:text-white transition-colors duration-300">
                    <Folder size={24} />
                  </div>
                  <ChevronRight className="text-gray-300 group-hover:text-orange-500 transition-colors" size={20} />
                </div>

                <div className="mt-auto">
                  <h3 className="text-xl font-bold text-gray-900 capitalize mb-1 group-hover:text-orange-600 transition-colors">
                    {deck.replace(/_/g, ' ')}
                  </h3>
                  <p className="text-sm text-gray-500">
                    /slide/{deck}
                  </p>
                </div>
              </div>
            </Link>
          ))}

          {slideDecks.length === 0 && (
            <div className="col-span-full text-center py-12 bg-white rounded-2xl border border-dashed border-gray-200 text-gray-500">
              <Folder className="mx-auto mb-3 text-gray-300" size={48} />
              <p>No slide decks found.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
