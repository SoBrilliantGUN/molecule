# ===== Atoms 前端镜像（多阶段：node 构建 → nginx 托管 + /api 反代）=====

# ---- 构建阶段 ----
FROM node:24-alpine AS build
WORKDIR /app

# 国内镜像：pnpm/npm 拉依赖都走 npmmirror（corepack 下载 pnpm 独立二进制在 ECS 上会被墙）
COPY .npmrc .npmrc

# 用 npm 装 pnpm（走 .npmrc 的镜像），避开 corepack 下载独立二进制
COPY package.json pnpm-workspace.yaml ./
RUN npm install -g pnpm@12.3.4 && pnpm install

# 拷贝源码并构建
COPY . .
RUN pnpm build

# ---- 运行阶段 ----
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
