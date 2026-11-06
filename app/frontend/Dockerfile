# ===== Atoms 前端镜像（多阶段：node 构建 → nginx 托管 + /api 反代）=====

# ---- 构建阶段 ----
FROM node:20-alpine AS build
WORKDIR /app

RUN corepack enable

# 先装依赖，利用缓存（无 lockfile，安装时生成）
COPY package.json ./
RUN corepack prepare pnpm@9.15.0 --activate && pnpm install

# 拷贝源码并构建
COPY . .
RUN pnpm build

# ---- 运行阶段 ----
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
