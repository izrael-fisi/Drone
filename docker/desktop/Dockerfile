FROM node:22-bookworm-slim

ENV NPM_CONFIG_UPDATE_NOTIFIER=false
ENV NPM_CONFIG_FUND=false
ENV NPM_CONFIG_AUDIT=false
ENV CHOKIDAR_USEPOLLING=true
ENV WATCHPACK_POLLING=true

WORKDIR /workspace/desktop-app

COPY desktop-app/package.json desktop-app/package-lock.json ./
RUN npm ci

COPY desktop-app ./

EXPOSE 5173 4173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
