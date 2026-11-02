import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Hisab AI — Digital Voice Khata for Indian Kirana Shopkeepers',
  description:
    'Hands-free voice digital ledger for Indian Kirana stores. Powered by Groq AI and Fast Intent Engine.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="hi" className="dark">
      <body className="bg-[#131315] text-[#F8FAFC] antialiased min-h-screen">
        {children}
      </body>
    </html>
  );
}
