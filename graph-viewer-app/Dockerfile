# Stage 1: Build the Vite app
FROM node:18-alpine AS build
WORKDIR /app

# Copy dependency files
COPY package.json package-lock.json ./
# Install clean dependencies
RUN npm ci --only=production && \
    npm cache clean --force

COPY . .

RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:alpine

# Clear default Nginx static assets
RUN rm -rf /usr/share/nginx/html/*
# Copy custom Nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist/. /usr/share/nginx/html/

HEALTHCHECK --interval=30s --timeout=3s \
    CMD wget --no-verbose --tries=1 --spider http://localhost/ || exit 1

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
