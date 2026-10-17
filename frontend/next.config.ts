import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  // Sinh bundle tối giản cho Docker: image production chỉ cần .next/standalone
  output: 'standalone',

  reactStrictMode: true,
  // Keep the dev badge away from the composer's sticky call to action.
  devIndicators: { position: 'bottom-right' },
  poweredByHeader: false,
  compress: true,

  eslint: {
    dirs: ['src'],
  },

  experimental: {
    // The /api rewrite otherwise aborts after 30s with a 500, while preparing
    // per-scene reference images legitimately takes one to three minutes.
    proxyTimeout: 600_000,
  },

  images: {
    // Thumbnail video được backend/nginx serve từ /storage
    remotePatterns: [
      { protocol: 'http', hostname: 'localhost' },
      { protocol: 'https', hostname: '**' },
    ],
  },

  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
        ],
      },
    ];
  },

  // Khi chạy `npm run dev` ngoài docker, proxy /api về backend để tránh CORS.
  // Trong docker thì nginx đảm nhiệm việc này.
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
