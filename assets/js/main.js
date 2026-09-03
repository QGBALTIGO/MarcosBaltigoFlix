(() => {
  'use strict';

  document.documentElement.classList.add('js');

  const header = document.querySelector('[data-header]');
  const menu = document.querySelector('[data-menu]');
  const menuToggle = document.querySelector('[data-menu-toggle]');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const setMenuState = (isOpen) => {
    if (!menu || !menuToggle) return;

    menu.classList.toggle('is-open', isOpen);
    menuToggle.setAttribute('aria-expanded', String(isOpen));
    document.body.classList.toggle('menu-open', isOpen);

    const label = menuToggle.querySelector('.sr-only');
    if (label) label.textContent = isOpen ? 'Fechar menu' : 'Abrir menu';
  };

  if (menu && menuToggle) {
    menuToggle.addEventListener('click', () => {
      setMenuState(menuToggle.getAttribute('aria-expanded') !== 'true');
    });

    menu.addEventListener('click', (event) => {
      if (event.target.closest('a')) setMenuState(false);
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        setMenuState(false);
        menuToggle.focus();
      }
    });

    document.addEventListener('click', (event) => {
      if (
        menuToggle.getAttribute('aria-expanded') === 'true' &&
        !menu.contains(event.target) &&
        !menuToggle.contains(event.target)
      ) {
        setMenuState(false);
      }
    });

    window.addEventListener('resize', () => {
      if (window.innerWidth > 860) setMenuState(false);
    }, { passive: true });
  }

  const updateHeader = () => {
    if (header) header.classList.toggle('is-scrolled', window.scrollY > 18);
  };

  updateHeader();
  window.addEventListener('scroll', updateHeader, { passive: true });

  document.querySelectorAll('[data-year]').forEach((element) => {
    element.textContent = String(new Date().getFullYear());
  });

  const revealElements = [...document.querySelectorAll('[data-reveal]')];

  revealElements.forEach((element) => {
    const delay = Number.parseInt(element.dataset.delay || '0', 10);
    element.style.setProperty('--reveal-delay', `${Number.isFinite(delay) ? delay : 0}ms`);
  });

  if (reducedMotion || !('IntersectionObserver' in window)) {
    revealElements.forEach((element) => element.classList.add('is-visible'));
  } else {
    const revealObserver = new IntersectionObserver((entries, observer) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      });
    }, {
      threshold: 0.12,
      rootMargin: '0px 0px -40px'
    });

    revealElements.forEach((element) => revealObserver.observe(element));
  }

  const navigationLinks = [...document.querySelectorAll('.main-nav a[href^="#"]')]
    .filter((link) => link.getAttribute('href').length > 1);
  const sections = navigationLinks
    .map((link) => document.querySelector(link.getAttribute('href')))
    .filter(Boolean);

  if ('IntersectionObserver' in window && sections.length) {
    const sectionObserver = new IntersectionObserver((entries) => {
      const visibleEntry = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];

      if (!visibleEntry) return;

      navigationLinks.forEach((link) => {
        link.classList.toggle('is-active', link.getAttribute('href') === `#${visibleEntry.target.id}`);
      });
    }, {
      rootMargin: '-25% 0px -60%',
      threshold: [0.05, 0.2, 0.5]
    });

    sections.forEach((section) => sectionObserver.observe(section));
  }

  // Mantém apenas parâmetros de campanha permitidos nos links do checkout.
  // Nenhum dado pessoal é coletado ou armazenado pelo site.
  const campaignKeys = [
    'utm_source',
    'utm_medium',
    'utm_campaign',
    'utm_content',
    'utm_term',
    'src'
  ];
  const currentParams = new URLSearchParams(window.location.search);

  document.querySelectorAll('.checkout-link').forEach((link) => {
    try {
      const checkoutUrl = new URL(link.href);
      campaignKeys.forEach((key) => {
        const value = currentParams.get(key);
        if (value && value.length <= 180) checkoutUrl.searchParams.set(key, value);
      });
      link.href = checkoutUrl.toString();
    } catch {
      // Mantém a URL original caso o navegador não consiga interpretá-la.
    }
  });
})();
