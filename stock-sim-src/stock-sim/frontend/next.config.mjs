/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone", // docker 이미지: node server.js 하나로 실행
  reactStrictMode: true,
};

export default nextConfig;
