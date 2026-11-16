/** Types propres à l'onglet IA (chantier bt-navigation). */

export interface Fournisseur { id: string; label: string; help: string; configured: boolean; default_model: string;
  models: { id: string; label: string }[]; }
export interface McpTok { id: number; name: string; prefix: string; scope: string; building_id: number | null;
  portfolio_id: number | null; expires_at: string; last_used_at: string | null; revoked: boolean; }
export interface McpInfo { url: string; claude: string; vibe: string; }
export interface IaReglages { providers: Fournisseur[]; preferences: { provider: string; models: Record<string, string> }; vault: boolean; }

/** Document d'un bâtiment (même forme que GET batiments/<id>/documents/). */
export interface DocBat { id: number; kind: string; filename: string; size_bytes: number; n_pages: number; pages_with_text: number[];
  stored: boolean; note?: string | null; }

/** GET parcs/<id>/documents/ (contrat § 2.5). */
export interface DocsParcLigne { building_id: number; building_name: string; documents: DocBat[]; propositions_en_attente: number; }

/** Fichier en attente d'envoi (import pour tout le parc). */
export interface EnvoiFichier { cle: number; fichier: File; batiment: number | null; etat: 'attente' | 'envoi' | 'ok' | 'erreur'; message: string; }
