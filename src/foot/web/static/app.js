// Petites animations du site : apparition au défilement, compteurs, ouverture du chat.
(() => {
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // 1) Les éléments .reveal apparaissent en glissant quand ils entrent à l'écran.
  const items = document.querySelectorAll(".reveal");
  if (calm || !("IntersectionObserver" in window)) {
    items.forEach((el) => el.classList.add("is-visible"));
  } else {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) {
          e.target.classList.add("is-visible");
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.12 });
    items.forEach((el, i) => {
      el.style.transitionDelay = `${Math.min(i % 6, 5) * 60}ms`; // léger décalage en cascade
      io.observe(el);
    });
  }

  // 2) Les compteurs [data-count] défilent de 0 à leur valeur.
  if (!calm) {
    document.querySelectorAll("[data-count]").forEach((el) => {
      const target = Number(el.dataset.count) || 0;
      const start = performance.now();
      const duration = 1400;
      const tick = (now) => {
        const t = Math.min(1, (now - start) / duration);
        const eased = 1 - Math.pow(1 - t, 3);
        el.textContent = Math.round(target * eased).toLocaleString("fr-FR");
        if (t < 1) requestAnimationFrame(tick);
      };
      el.textContent = "0";
      requestAnimationFrame(tick);
    });
  }

  // 3) Les boutons [data-open-chat] ouvrent l'assistant.
  document.querySelectorAll("[data-open-chat]").forEach((b) =>
    b.addEventListener("click", () => document.getElementById("chat-toggle")?.click()));
})();
