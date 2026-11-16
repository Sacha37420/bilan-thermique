/** Formes de l'API des protections solaires (catalogue v2, servi par le serveur). */
export type Valeur = number | boolean | string | null;

export interface Parametre {
  cle: string; libelle: string; unite: string; type: 'number' | 'int' | 'enum' | 'bool';
  min: number | null; max: number | null; pas: number | null; defaut: Valeur;
  choix: { valeur: string; libelle: string }[] | null; groupe: string; aide: string; niveaux: string[];
}
export interface Modele {
  cle: string; famille: string; libelle: string; description: string; modes: string[];
  parametres: Parametre[]; pilotage: { modes: string[]; parametres: Parametre[] };
  application: Record<string, string>;
}
export interface Catalogue { version: number; familles: { cle: string; libelle: string }[]; modeles: Modele[]; }

export interface Protection {
  id: number; kind: string; orientation: string; mode: string; label: string;
  params: Record<string, Valeur>; control: Record<string, Valeur>; scenario: number | null;
  famille: string; libelle: string; created_at: string;
}
export interface Amelioration { id: number; name: string; genere: boolean; }

export interface EffectRow {
  label: string; device_id: number | null; heating_kwh: number; hours_over_26: number; hours_over_28: number;
  degree_hours_26: number; dh_re2020: number; heures_inconfort: number; t_op_max: number;
  solar_by_orientation_kwh: Record<string, number>;
}
export interface Groupe { nom: string; parametres: Parametre[]; }
