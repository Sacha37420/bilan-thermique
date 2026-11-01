import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { from, switchMap, catchError } from 'rxjs';
import { KeycloakService } from './keycloak.service';
import { tokenBases } from './env';
import { carriesToken } from './token-scope';

/**
 * Joint le jeton de l'utilisateur aux appels vers l'API de l'app — et à elle
 * seule (`tokenBases()`). Une requête vers toute autre adresse part telle
 * quelle, sans jeton.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  if (!carriesToken(req.url, tokenBases(), window.location.origin)) return next(req);

  const kc = inject(KeycloakService);
  const token = kc.getToken();

  // Chemin synchrone : token présent et valide — aucune attente async
  if (token && !kc.isTokenExpired(30)) {
    return next(req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }));
  }

  // Chemin async : token expiré ou absent — tentative de refresh
  return from(kc.updateToken(-1)).pipe(
    switchMap(() => {
      const fresh = kc.getToken();
      if (fresh) req = req.clone({ setHeaders: { Authorization: `Bearer ${fresh}` } });
      return next(req);
    }),
    catchError(() => next(req)),
  );
};
