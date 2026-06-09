FROM mcr.microsoft.com/playwright:v1.53.0-jammy

WORKDIR /app

COPY package.json ./
RUN npm install
RUN npx playwright install chromium firefox webkit

COPY . .

CMD ["npm", "run", "start"]
