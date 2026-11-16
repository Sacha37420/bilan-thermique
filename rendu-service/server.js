// Point d'entrée du conteneur bilan-thermique-rendu (interface I8).
// Écoute 0.0.0.0:3000 sur le réseau interne de l'app ; aucun port publié.

import http from 'node:http';
import { creerGestionnaire, lireCle } from './app.js';
import { chargerBibliotheques } from './libs.js';
import { creerRendu } from './renderer.js';

/** Démarre le service ; rend { serveur, rendu, fermer }. */
export async function demarrer({
  env = process.env, port = 3000, hote = '0.0.0.0', surBloque, journal,
} = {}) {
  const cle = lireCle(env);
  const libs = chargerBibliotheques();
  const rendu = creerRendu({ libs, surBloque });
  const log = journal || ((e) => process.stdout.write(`${JSON.stringify(e)}\n`));
  const serveur = http.createServer(creerGestionnaire({ cle, rendu, journal: log }));
  serveur.requestTimeout = 120_000;
  serveur.headersTimeout = 20_000;
  await new Promise((resolve, reject) => {
    serveur.once('error', reject);
    serveur.listen(port, hote, resolve);
  });
  // Lancement anticipé du navigateur (un échec ici n'empêche pas de répondre :
  // il sera retenté à la première requête, qui rendra 503 s'il échoue encore).
  rendu.obtenirNavigateur().catch(() => log({ evenement: 'navigateur_indisponible' }));
  return {
    serveur,
    rendu,
    async fermer() {
      await new Promise((r) => { serveur.close(() => r()); serveur.closeAllConnections(); });
      await rendu.fermer();
    },
  };
}

const principal = import.meta.url === `file://${process.argv[1]}`;
if (principal) {
  let service;
  try {
    service = await demarrer();
  } catch (err) {
    // Message de lireCle (fixe) ou d'écoute ; jamais la valeur de la clé.
    process.stderr.write(`Démarrage refusé : ${err.message}\n`);
    process.exit(1);
  }
  process.stdout.write(`${JSON.stringify({ evenement: 'pret', port: 3000 })}\n`);
  const arret = async () => {
    await service.fermer().catch(() => {});
    process.exit(0);
  };
  process.on('SIGTERM', arret);
  process.on('SIGINT', arret);
}
