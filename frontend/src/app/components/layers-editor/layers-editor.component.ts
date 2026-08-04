import { Component, model } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';

export interface LayerInput {
  e: number;
  lam: number;
  rho: number;
  c: number;
  tau: number;
  r: number;
  alpha: number;
}

export function defaultLayer(): LayerInput {
  return { e: 0.05, lam: 0.5, rho: 800, c: 1000, tau: 0, r: 0.9, alpha: 0.1 };
}

@Component({
  selector: 'app-layers-editor',
  standalone: true,
  imports: [FormsModule, DecimalPipe],
  templateUrl: './layers-editor.component.html',
  styleUrl: './layers-editor.component.scss',
})
export class LayersEditorComponent {
  layers = model.required<LayerInput[]>();

  addLayer(): void {
    this.layers.update(list => [...list, defaultLayer()]);
  }

  removeLayer(i: number): void {
    this.layers.update(list => list.filter((_, idx) => idx !== i));
  }

  layerSumWarning(l: LayerInput): boolean {
    return Math.abs(l.tau + l.r + l.alpha - 1) > 0.01;
  }
}
