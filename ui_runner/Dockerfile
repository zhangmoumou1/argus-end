FROM mcr.microsoft.com/playwright:v1.53.0-jammy

WORKDIR /app

COPY package.json ./
RUN npm install
RUN npx playwright install chromium firefox webkit

COPY . .
RUN mkdir -p /app/logs/startup

CMD ["sh", "-c", "exec npm run start 2>&1 | tee -a /app/logs/startup/argus-ui-runner.log"]
