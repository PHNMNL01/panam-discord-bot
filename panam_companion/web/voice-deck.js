"use strict";
(function (root) {
  const ACTIONS = ["listen", "start", "mute", "unmute", "end", "stop-listening"];
  const LABELS = {LISTENING_OFF: "LISTENING_OFF · lokální poslech vypnutý", LOCAL_LISTENING: "LOCAL_LISTENING · lokální poslech", ACTIVE: "ACTIVE · cloudový hovor"};
  const OPERATIONS = {listen: "STARTING · otevírám mikrofon", start: "STARTING · připravuji placený hovor", mute: "Ztlumuji", unmute: "Obnovuji mikrofon", end: "CLOSING · ukončuji hovor", "stop-listening": "CLOSING · uvolňuji prostředky"};

  function view(status, pending = false) {
    if (!status) return {title: "Runtime status unavailable", description: "Stav hovoru není známý. Python runtime může pokračovat.", allowed: []};
    const uncertain = status.last_call && status.last_call.closure_confirmed !== true;
    let title = LABELS[status.lifecycle_state] || "Neznámý stav runtime";
    let description = status.lifecycle_state === "ACTIVE" ? "Zvuk obsluhuje Python runtime a headset." : "Žádná aktivní cloudová konverzace.";
    if (status.lifecycle_state === "LISTENING_OFF") description = "Start Listening otevře lokální mikrofon. Cloud se automaticky nepřipojí.";
    if (status.lifecycle_state === "LOCAL_LISTENING") description = "Mikrofon je otevřený; vstup bez hovoru se nezachovává.";
    if (status.fault || status.cleanup_failed || uncertain) {
      title = "FAULT / UNCERTAIN CLEANUP";
      description = "Zkontrolujte bezpečnou diagnostiku. Nepotvrzené uzavření není úspěšné ukončení.";
    }
    if (status.session_closing) title = "CLOSING · čekám na uzavření";
    if (status.current_operation) title = OPERATIONS[status.current_operation] || "Probíhá operace";
    if (status.shutting_down) title = "CLOSING · Python runtime se ukončuje";
    return {title, description, allowed: pending || status.current_operation || status.shutting_down ? [] : ACTIONS.filter(a => status.allowed_commands.includes(a))};
  }

  function attach(document, deps) {
    let status = null, submitting = false, polling = false, stopped = false, timer = null, generation = 0;
    const element = id => document.getElementById(id);
    const buttons = [...document.querySelectorAll("[data-action]")];
    function render() {
      const model = view(status, submitting);
      element("state").textContent = model.title;
      element("description").textContent = model.description;
      for (const button of buttons) {
        button.disabled = !model.allowed.includes(button.dataset.action);
        button.hidden = !!status && status.lifecycle_state !== "ACTIVE" && ["mute", "unmute", "end"].includes(button.dataset.action) && !status.current_operation && !model.allowed.includes(button.dataset.action);
      }
      element("microphone").textContent = status ? (status.microphone_owned ? (status.muted ? "Otevřený · ztlumený" : "Otevřený") : "Uzavřený") : "Neznámý stav";
      element("cloud").textContent = !status ? "Neznámý stav" : status.cloud_ready ? "ACTIVE / připravený" : status.cloud_session_owned ? "Relace vlastněna · připravenost nepotvrzena" : "Žádná vlastněná relace";
      element("output").textContent = status ? (status.output_owned ? "Otevřený" : "Uzavřený") : "Neznámý stav";
      const elapsed = status && status.elapsed_session_seconds;
      element("elapsed").textContent = typeof elapsed === "number" && Number.isFinite(elapsed) ? `${Math.floor(elapsed / 60)}:${String(Math.floor(elapsed % 60)).padStart(2, "0")}` : "—";
      element("last-call").textContent = status && status.last_call ? JSON.stringify(status.last_call, null, 2) : "Zatím žádný záznam.";
      if (status && status.last_command_outcome) {
        const outcome = status.last_command_outcome;
        element("feedback").textContent = outcome.status === "ACCEPTED" ? "Příkaz přijat; čekám na potvrzení runtime." : outcome.status === "SUCCEEDED" ? "Operace potvrzena runtime; stav uzavření je uveden v diagnostice." : "Operace selhala nebo uzavření není potvrzené.";
      }
    }
    async function poll() {
      if (polling || stopped) return;
      polling = true;
      const observedGeneration = generation;
      try {
        const response = await deps.fetch("/api/status", {cache: "no-store"});
        if (!response.ok) throw new Error("status");
        const received = await response.json();
        if (!Array.isArray(received.allowed_commands) || typeof received.runtime_instance_id !== "string" || !Number.isSafeInteger(received.control_revision)) throw new Error("status schema");
        if (!submitting && observedGeneration === generation) status = received;
      } catch (_) {
        if (observedGeneration === generation) status = null;
      } finally {
        polling = false;
        render();
        if (!stopped) timer = deps.setTimeout(poll, 1000);
      }
    }
    async function command(action) {
      if (submitting || !status || !view(status).allowed.includes(action)) return;
      if (action === "start" && !deps.confirm("Spustit jednu placenou GPT-Live relaci s mikrofonem a výstupem headsetu?")) return;
      const payload = {command_id: deps.uuid(), runtime_instance_id: status.runtime_instance_id,
        expected_control_revision: status.control_revision, action};
      if (action === "start") payload.paid_call_consent = true;
      generation += 1;
      submitting = true;
      render();
      try {
        const response = await deps.fetch("/api/commands", {method: "POST", headers: {"Content-Type": "application/json", "X-Panam-Command": "1"}, body: JSON.stringify(payload)});
        element("feedback").textContent = response.status === 202 ? "Příkaz přijat; jeho dokončení potvrzuje až stav runtime." : "Příkaz odmítnut. Obnovte stav a rozhodněte ručně.";
      } catch (_) {
        element("feedback").textContent = "Výsledek odeslání není známý. Příkaz se nebude automaticky opakovat.";
      } finally {
        status = null; // Never re-enable actions using the pre-command snapshot.
        submitting = false;
        render();
      }
    }
    for (const button of buttons) button.addEventListener("click", () => command(button.dataset.action));
    render();
    poll();
    return {poll, command, stop: () => { stopped = true; deps.clearTimeout(timer); }};
  }
  const api = {view, attach};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else attach(root.document, {fetch: root.fetch.bind(root), confirm: root.confirm.bind(root), uuid: () => root.crypto.randomUUID(), setTimeout: root.setTimeout.bind(root), clearTimeout: root.clearTimeout.bind(root)});
})(typeof window !== "undefined" ? window : globalThis);
