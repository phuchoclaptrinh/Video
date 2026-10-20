import type { Metadata, Viewport } from 'next';
import { Inter } from 'next/font/google';
import './globals.css';
import './workflow.css';
import './landing.css';

const sans = Inter({ subsets: ['latin', 'vietnamese'], variable: '--font-sans', display: 'swap' });

export const metadata: Metadata = {
  title: 'VO3 Studio',
  description: 'Tạo video quảng cáo có kịch bản từ ảnh sản phẩm',
  // The UI is already Vietnamese; browser translation rewrites text nodes React still owns.
  other: { google: 'notranslate' },
};
export const viewport: Viewport = { themeColor: '#ffffff' };

// Translators and some extensions replace text nodes (e.g. with <font>), after which React's
// insertBefore/removeChild on those nodes throws and the whole page crashes. Fall back to a safe
// DOM operation instead (workaround from facebook/react#11538).
const domGuard = `(function(){var p=typeof Node==='function'&&Node.prototype;if(!p||p.__vo3Guard)return;p.__vo3Guard=true;
var remove=p.removeChild;p.removeChild=function(child){if(child&&child.parentNode!==this)return child;return remove.apply(this,arguments)};
var insert=p.insertBefore;p.insertBefore=function(node,before){if(before&&before.parentNode!==this)return insert.call(this,node,null);return insert.apply(this,arguments)}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi" translate="no" className={sans.variable}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: domGuard }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
