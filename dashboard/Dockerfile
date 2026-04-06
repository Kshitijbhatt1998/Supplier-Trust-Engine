# Stage 1 — build the React app
FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# No API key baked into the JS bundle — nginx injects it at the proxy layer.
RUN npm run build

# Stage 2 — serve with nginx
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html

# Place config as a template; the official nginx image's entrypoint runs
# envsubst on /etc/nginx/templates/*.template → /etc/nginx/conf.d/*.conf
COPY nginx.conf /etc/nginx/templates/default.conf.template

# nginx entrypoint will substitute ${API_KEY} from the container's env
EXPOSE 80
