import type { NextConfig } from 'next';
import path from 'path';

const nextConfig: NextConfig = {
  transpilePackages: ['@aip/design-system'],
  // 모노레포에서 Next.js가 워크스페이스 루트를 정확히 추론하도록 명시
  // (route group + Vercel 조합의 manifest 누락 버그 회피)
  outputFileTracingRoot: path.join(__dirname, '../../'),
};

export default nextConfig;
