import { WorkingTriangle } from './building.types';

/**
 * Générateur de bâtiment "boîte" (Lot O) — alternative à l'import OBJ/STL
 * pour tester l'outil sans modélisateur 3D externe. Géométrie triviale,
 * calculée directement côté client (pas de nouvel endpoint).
 *
 * Repère : Z-up, +Y = nord, +X = est (même convention que api.geometry —
 * voir son docstring). Empreinte centrée sur l'origine.
 */

export interface BoxParams {
  width: number;           // m, dimension est-ouest (axe X)
  length: number;          // m, dimension nord-sud (axe Y)
  height: number;          // m, hauteur des murs (égout)
  roof: 'flat' | 'gable';
  roofPitchHeight: number; // m, hauteur du faîtage au-dessus de l'égout — utilisé seulement si roof==='gable'
}

export interface GeneratedMesh {
  vertices: number[][];
  triangles: WorkingTriangle[];
  groups: string[];
}

export function generateBoxEnvelope(p: BoxParams): GeneratedMesh {
  const hw = p.width / 2;
  const hl = p.length / 2;
  const h = p.height;

  const vertices: number[][] = [];
  const push = (x: number, y: number, z: number): number => {
    vertices.push([x, y, z]);
    return vertices.length - 1;
  };

  // Sol (z=0) et égout (z=h) — winding vérifié à la main (produit vectoriel)
  // pour que chaque groupe pointe bien vers l'extérieur : voir to_do.md, Lot O.
  const A_sw = push(-hw, -hl, 0), A_se = push(hw, -hl, 0), A_ne = push(hw, hl, 0), A_nw = push(-hw, hl, 0);
  const B_sw = push(-hw, -hl, h), B_se = push(hw, -hl, h), B_ne = push(hw, hl, h), B_nw = push(-hw, hl, h);

  const triangles: WorkingTriangle[] = [];
  const tri = (group: string, boundary: 'exterior_air' | 'ground', a: number, b: number, c: number): void => {
    triangles.push({ v: [a, b, c], group, paroi_model_id: null, boundary });
  };

  tri('sol', 'ground', A_sw, A_nw, A_ne);
  tri('sol', 'ground', A_sw, A_ne, A_se);

  // Murs est/ouest : toujours rectangulaires, même en toiture 2 pans — le
  // faîtage court nord-sud, le pignon (triangle du dessus) est porté par les
  // murs nord/sud, pas est/ouest.
  tri('mur_est', 'exterior_air', A_se, A_ne, B_ne);
  tri('mur_est', 'exterior_air', A_se, B_ne, B_se);
  tri('mur_ouest', 'exterior_air', A_nw, A_sw, B_sw);
  tri('mur_ouest', 'exterior_air', A_nw, B_sw, B_nw);

  if (p.roof === 'flat') {
    tri('mur_sud', 'exterior_air', A_sw, A_se, B_se);
    tri('mur_sud', 'exterior_air', A_sw, B_se, B_sw);
    tri('mur_nord', 'exterior_air', A_ne, A_nw, B_nw);
    tri('mur_nord', 'exterior_air', A_ne, B_nw, B_ne);
    tri('toiture', 'exterior_air', B_sw, B_se, B_ne);
    tri('toiture', 'exterior_air', B_sw, B_ne, B_nw);
    return { vertices, triangles, groups: ['sol', 'mur_est', 'mur_ouest', 'mur_sud', 'mur_nord', 'toiture'] };
  }

  // Toiture 2 pans : faîtage le long de Y (à x=0), pignons sud/nord — les murs
  // sud/nord deviennent des pentagones (rectangle + triangle de pignon).
  const pitch = Math.max(p.roofPitchHeight, 0.01);
  const R_s = push(0, -hl, h + pitch), R_n = push(0, hl, h + pitch);

  tri('mur_sud', 'exterior_air', A_sw, A_se, B_se);
  tri('mur_sud', 'exterior_air', A_sw, B_se, B_sw);
  tri('mur_sud', 'exterior_air', B_sw, B_se, R_s);

  tri('mur_nord', 'exterior_air', A_ne, A_nw, B_nw);
  tri('mur_nord', 'exterior_air', A_ne, B_nw, B_ne);
  tri('mur_nord', 'exterior_air', B_ne, B_nw, R_n);

  tri('toiture_est', 'exterior_air', B_se, B_ne, R_n);
  tri('toiture_est', 'exterior_air', B_se, R_n, R_s);
  tri('toiture_ouest', 'exterior_air', B_nw, B_sw, R_s);
  tri('toiture_ouest', 'exterior_air', B_nw, R_s, R_n);

  return {
    vertices, triangles,
    groups: ['sol', 'mur_est', 'mur_ouest', 'mur_sud', 'mur_nord', 'toiture_est', 'toiture_ouest'],
  };
}
