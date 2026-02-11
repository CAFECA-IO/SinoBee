import HeroSection from '@/components/landing/hero_section';
import FeatureHexGrid from '@/components/landing/feature_hex_grid';
import Footer from '@/components/landing/footer';

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-between">
      <HeroSection />
      <FeatureHexGrid />
      <Footer />
    </main>
  );
}
