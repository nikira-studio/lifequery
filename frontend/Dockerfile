FROM node:22-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .

# Build with optional API URL
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

# Use nginx to serve static files and proxy API requests
FROM nginx:alpine
# Copy custom nginx config
COPY nginx.conf /etc/nginx/nginx.conf
# Copy built static files
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
