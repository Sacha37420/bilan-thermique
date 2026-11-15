import { Component, ElementRef, HostListener, inject, signal, ViewChild } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';
import { KeycloakService } from './core/keycloak.service';
import { ThemeService } from './core/theme.service';

interface NavItem {
  label: string;
  abbr: string;
  path: string;
  exact?: boolean;
}

const MOBILE_CLOSE_ANIM_MS = 220;

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive, NgTemplateOutlet],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class AppComponent {
  protected kc = inject(KeycloakService);
  protected theme = inject(ThemeService);

  collapsed = signal(false);
  mobileOpen = signal(false);
  mobileClosing = signal(false);

  protected noop = (): void => {};
  protected closeMobileFn = (): void => this.closeMobile();

  // Lot 27 (refonte) : un bâtiment s'étudie dans sa fiche (maillage 3D, environnement, simulation,
  // calculs) ; les entrées suivantes sont des outils experts.
  readonly navItems: NavItem[] = [
    { path: '/parc',          label: 'Parcs',            abbr: 'Pc' },
    { path: '/fiche',         label: 'Fiche bâtiment',   abbr: 'Fi' },
    { path: '/ia',            label: 'Intelligence artificielle', abbr: 'IA' },
    { path: '/calcul-1d',     label: 'Calcul 1D (paroi)', abbr: '1D' },
    { path: '/parois',        label: 'Modèles de paroi', abbr: 'Mp' },
    { path: '/batiment',      label: 'Importer un maillage', abbr: 'Im' },
    { path: '/environnement', label: 'Environnement d’un quartier', abbr: 'En' },
    { path: '/calcul-3d',     label: 'Calcul 3D avancé', abbr: '3D' },
    { path: '/theorie',       label: 'Théorie',          abbr: 'Th' },
  ];

  @ViewChild('closeBtn') private closeBtnRef?: ElementRef<HTMLButtonElement>;
  @ViewChild('burgerBtn') private burgerBtnRef?: ElementRef<HTMLButtonElement>;

  toggleCollapsed(): void {
    this.collapsed.update(v => !v);
  }

  openMobile(): void {
    this.mobileOpen.set(true);
    this.mobileClosing.set(false);
    document.body.style.overflow = 'hidden';
    setTimeout(() => this.closeBtnRef?.nativeElement.focus());
  }

  closeMobile(): void {
    if (!this.mobileOpen() || this.mobileClosing()) return;
    this.mobileClosing.set(true);
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    setTimeout(() => {
      this.mobileOpen.set(false);
      this.mobileClosing.set(false);
      document.body.style.overflow = '';
      this.burgerBtnRef?.nativeElement.focus();
    }, reduced ? 0 : MOBILE_CLOSE_ANIM_MS);
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    if (this.mobileOpen()) this.closeMobile();
  }

  get username(): string {
    return this.kc.username || this.kc.email;
  }

  logout(): void {
    this.kc.logout();
  }
}

// Export pour Angular standalone
export { AppComponent as App };
