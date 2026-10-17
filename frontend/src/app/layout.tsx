import type { Metadata, Viewport } from 'next';
import { Inter } from 'next/font/google';
import './globals.css';
import './workflow.css';

const sans = Inter({ subsets: ['latin', 'vietnamese'], variable: '--font-sans', display: 'swap' });

export const metadata: Metadata = {
  title: 'VO3 Studio',
  description: 'Tạo video quảng cáo có kịch bản từ ảnh sản phẩm',
};
export const viewport: Viewport = { themeColor: '#ffffff' };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi" className={sans.variable}>
      <body>{children}</body>
    </html>
  );
}
