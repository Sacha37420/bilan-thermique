import { ApplicationConfig, APP_INITIALIZER, LOCALE_ID } from '@angular/core';
import { registerLocaleData } from '@angular/common';
import localeFr from '@angular/common/locales/fr';
import { provideRouter } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { routes } from './app.routes';
import { KeycloakService } from './core/keycloak.service';
import { authInterceptor } from './core/auth.interceptor';

// Toute l'interface est en français, mais Angular formate par défaut en en-US :
// `| number` rendait « 4,210 kWh » là où il faut lire « 4 210 kWh » — une virgule
// qui, dans un affichage de résultats chiffrés, se lit comme un séparateur
// décimal. Constaté en vérifiant le mode simplifié (2026-08-09).
registerLocaleData(localeFr);

function initKeycloak(kc: KeycloakService): () => Promise<boolean> {
  return () => kc.init();
}

export const appConfig: ApplicationConfig = {
  providers: [
    provideRouter(routes),
    { provide: LOCALE_ID, useValue: 'fr-FR' },
    provideHttpClient(withInterceptors([authInterceptor])),
    {
      provide:    APP_INITIALIZER,
      useFactory: initKeycloak,
      deps:       [KeycloakService],
      multi:      true,
    },
  ],
};
