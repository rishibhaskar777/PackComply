FROM node:20-alpine

# Public browser URL of the API; baked in at build time and read again by the dev server.
ARG VITE_API_URL=http://localhost:8000/api
ENV VITE_API_URL=${VITE_API_URL}

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci && npm cache clean --force
COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev"]
