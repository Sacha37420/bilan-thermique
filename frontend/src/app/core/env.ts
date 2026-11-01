/**
 * Configuration injectée au démarrage du conteneur (`assets/env.js`, écrit par
 * `nginx-entrypoint.sh`).
 */
export interface LabEnv {
  keycloakUrl?: string;
  keycloakRealm?: string;
  keycloakClientId?: string;
  appUrl?: string;
  apiUrl?: string;
}

export function labEnv(): LabEnv {
  return (window as unknown as { __env?: LabEnv }).__env ?? {};
}

/** Base de l'API de bilan-thermique (`…/bilan-thermique-api`). */
export function apiUrl(): string {
  return labEnv().apiUrl ?? 'http://localhost:8099';
}

/**
 * Les SEULES adresses auxquelles le jeton de l'utilisateur est joint
 * (`auth.interceptor.ts`, `token-scope.ts`) : l'API de l'app, rien d'autre —
 * cette interface n'appelle aucun autre service avec le jeton (IGN, Open-Meteo,
 * Panoramax sont appelés par le backend ; les textures sont des `blob:` tirés
 * de l'API).
 */
export function tokenBases(): string[] {
  return [apiUrl()];
}
