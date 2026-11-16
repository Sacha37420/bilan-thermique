# Service de rendu de bilan-thermique (interface I8) : PDF, images Plotly, vues 3D.
# Chromium isolé dans sa propre image, jamais dans l'image Django (même règle que
# runner/ et robot-lab/engine). Image officielle Playwright : Node + navigateurs
# déjà installés, aucun `playwright install` au build.
FROM mcr.microsoft.com/playwright:v1.48.0-jammy

ENV NODE_ENV=production

WORKDIR /app

# Verrou généré avec --before=2026-10-30 (publié depuis plus de deux semaines).
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force

COPY server.js app.js validation.js renderer.js libs.js vue3d-page.js ./
COPY test ./test

# Utilisateur non root fourni par l'image (uid 1000).
USER pwuser

EXPOSE 3000
CMD ["node", "server.js"]
