// Bulle de chat : envoie la question à /api/chat et affiche la réponse.
(() => {
  const toggle = document.getElementById("chat-toggle");
  const panel = document.getElementById("chat-panel");
  const log = document.getElementById("chat-log");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const suggestions = document.getElementById("chat-suggestions");
  const history = []; // { role: "user" | "assistant", content }

  const open = (yes) => {
    panel.hidden = !yes;
    toggle.setAttribute("aria-expanded", String(yes));
    if (yes) input.focus();
  };
  toggle.addEventListener("click", () => open(panel.hidden));
  document.getElementById("chat-close").addEventListener("click", () => open(false));

  // Markdown très simple et SÛR : on échappe tout le HTML d'abord, puis on
  // n'autorise que le gras, les listes et les liens vers le site ou en https.
  const escape = (s) => s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  function render(md) {
    let html = escape(md)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]]+)\]\(((?:\/|https:\/\/)[^)\s]*)\)/g, (_, text, url) => {
        const external = url.startsWith("https://");
        return `<a href="${url}"${external ? ' target="_blank" rel="noopener"' : ""}>${text}</a>`;
      });
    const blocks = html.split(/\n{2,}/).map((block) => {
      const lines = block.split("\n");
      if (lines.every((l) => /^\s*[*-]\s+/.test(l))) {
        return "<ul>" + lines.map((l) => `<li>${l.replace(/^\s*[*-]\s+/, "")}</li>`).join("") + "</ul>";
      }
      return `<p>${lines.map((l) => l.replace(/^\s*[*-]\s+/, "• ")).join("<br>")}</p>`;
    });
    return blocks.join("");
  }

  function add(role, content, extra = "", faces = {}) {
    const div = document.createElement("div");
    div.className = `msg ${role} ${extra}`.trim();
    if (role === "bot") {
      div.innerHTML = render(content);
      // Tête du joueur devant chaque lien vers sa fiche (photos Wikimedia uniquement).
      div.querySelectorAll('a[href^="/joueur/"]').forEach((a) => {
        const url = faces[a.getAttribute("href")];
        if (typeof url === "string" && url.startsWith("https://upload.wikimedia.org/")) {
          const img = document.createElement("img");
          img.className = "face";
          img.src = url;
          img.alt = "";
          img.onerror = () => img.remove();
          a.prepend(img);
        }
      });
    } else div.textContent = content;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  async function ask(question) {
    if (suggestions) suggestions.remove();
    add("user", question);
    // Indicateur « l'IA réfléchit » : trois points lumineux.
    const typing = document.createElement("div");
    typing.className = "msg bot typing";
    typing.setAttribute("aria-label", "L'assistant réfléchit");
    typing.innerHTML = "<i></i><i></i><i></i>";
    log.appendChild(typing);
    log.scrollTop = log.scrollHeight;
    form.querySelector("button").disabled = true;
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question, history: history.slice(-6) }),
      });
      const data = await res.json().catch(() => ({}));
      const answer = res.ok ? data.answer : (data.detail || "Une erreur est survenue, réessaie.");
      typing.remove();
      add("bot", answer, "", (res.ok && data.faces) || {});
      if (res.ok) history.push({ role: "user", content: question }, { role: "assistant", content: answer });
    } catch {
      typing.remove();
      add("bot", "Impossible de joindre le serveur. Vérifie ta connexion.");
    } finally {
      form.querySelector("button").disabled = false;
      input.focus();
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    input.value = "";
    ask(q);
  });
  suggestions?.querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => ask(b.textContent)));
})();
