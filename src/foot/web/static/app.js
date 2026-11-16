// Animations au défilement, légères (aucune librairie) :
//  - [data-reveal] : le bloc apparaît en glissant quand il entre à l'écran ;
//  - [data-slide]  : le mot géant défile horizontalement au rythme du défilement ;
//  - [data-count]  : le chiffre compte jusqu'à sa valeur quand on le voit.
// Tout est désactivé si le visiteur a demandé moins d'animations.
(() => {
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const canObserve = "IntersectionObserver" in window;

  // 1) Apparition
  const reveals = document.querySelectorAll("[data-reveal]");
  if (calm || !canObserve) {
    reveals.forEach((el) => el.classList.add("is-in"));
  } else {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (!e.isIntersecting) return;
        e.target.classList.add("is-in");
        io.unobserve(e.target);
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    reveals.forEach((el) => io.observe(el));
  }

  // 2) Compteurs (démarrent quand le chiffre devient visible)
  const count = (el) => {
    const target = Number(el.dataset.count) || 0;
    const start = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - start) / 1300);
      el.textContent = Math.round(target * (1 - Math.pow(1 - t, 3))).toLocaleString("fr-FR");
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };
  const counters = document.querySelectorAll("[data-count]");
  if (!calm && canObserve) {
    const co = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (!e.isIntersecting) return;
        count(e.target);
        co.unobserve(e.target);
      });
    }, { threshold: 0.4 });
    // Seuls les chiffres pas encore visibles partent de 0 : un robot (Google, IA) qui lit
    // la page sans la faire défiler voit toujours les vraies valeurs.
    counters.forEach((el) => {
      if (el.getBoundingClientRect().top > window.innerHeight) {
        el.textContent = "0";
        co.observe(el);
      }
    });
  }

  // 3) Mots géants qui glissent (transform uniquement : fluide sur téléphone)
  const sliders = [...document.querySelectorAll("[data-slide]")];
  if (!calm && sliders.length) {
    let ticking = false;
    const update = () => {
      const vh = window.innerHeight;
      sliders.forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.bottom < -200 || r.top > vh + 200) return; // hors écran : rien à faire
        const progress = (vh - r.top) / (vh + r.height);   // 0 en bas -> 1 en haut
        el.style.transform = `translate3d(${(-progress * 35).toFixed(2)}%, 0, 0)`;
      });
      ticking = false;
    };
    window.addEventListener("scroll", () => {
      if (!ticking) { ticking = true; requestAnimationFrame(update); }
    }, { passive: true });
    update();
  }

  // 4) Les boutons [data-open-chat] ouvrent l'assistant
  document.querySelectorAll("[data-open-chat]").forEach((b) =>
    b.addEventListener("click", () => document.getElementById("chat-toggle")?.click()));
})();
