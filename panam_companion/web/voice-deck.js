"use strict";
(function (root) {
  const ACTIONS = ["listen", "start", "mute", "unmute", "end", "stop-listening"];
  const LABELS = {LISTENING_OFF: "Listening Off", LOCAL_LISTENING: "Local Listening", ACTIVE: "In Conversation"};
  const OPERATIONS = {listen: "Starting", start: "Starting", mute: "Muting", unmute: "Unmuting", end: "Closing", "stop-listening": "Closing"};

  function view(status, pending = false) {
    if (!status) return {title: "Runtime status unavailable", description: "Stav hovoru není známý. Hovor může pokračovat i bez této karty.", tone: "disconnected", allowed: []};
    const uncertain = status.last_call && status.last_call.closure_confirmed !== true;
    let title = LABELS[status.lifecycle_state] || "Neznámý stav runtime";
    let tone = {LISTENING_OFF: "off", LOCAL_LISTENING: "listening", ACTIVE: "active"}[status.lifecycle_state] || "disconnected";
    let description = "Aktuální stav není známý. Hovor může pokračovat.";
    if (status.lifecycle_state === "ACTIVE") description = "Jsem tu. Můžeme si povídat přes váš headset.";
    if (status.lifecycle_state === "ACTIVE" && status.muted) { title = "Muted"; tone = "muted"; description = "Mikrofon je ztlumený. Panam můžete dál slyšet."; }
    if (status.lifecycle_state === "LISTENING_OFF") description = "Až budete chtít, zapněte mikrofon. Hovor se sám nespustí.";
    if (status.lifecycle_state === "LOCAL_LISTENING") description = "Mikrofon je připravený. Placený hovor spustíte tlačítkem Call.";
    if (status.fault || status.cleanup_failed || uncertain) {
      title = "Fault";
      tone = "fault";
      description = "Hovor nebo jeho uzavření vyžaduje kontrolu. Podrobnosti najdete v technických informacích.";
    }
    if (status.session_closing) { title = "Closing"; tone = "closing"; description = "Čekám na potvrzení uzavření hovoru."; }
    if (status.current_operation) {
      title = OPERATIONS[status.current_operation] || "Probíhá operace";
      tone = ["end", "stop-listening"].includes(status.current_operation) ? "closing" : "pending";
      description = "Čekám na potvrzení. Ovládání se zpřístupní po dokončení operace.";
    }
    if (status.shutting_down) { title = "Closing"; tone = "closing"; description = "Aplikace se ukončuje a uvolňuje prostředky."; }
    return {title, description, tone, allowed: pending || status.current_operation || status.shutting_down ? [] : ACTIONS.filter(a => status.allowed_commands.includes(a))};
  }

  function attach(document, deps) {
    let status = null, submitting = false, polling = false, stopped = false, timer = null, generation = 0;
    const element = id => document.getElementById(id);
    const buttons = [...document.querySelectorAll("[data-action]")];
    const admin = document.body?.dataset.surface === "admin";
    function render() {
      const model = view(status, submitting);
      if (element("deck")) element("deck").dataset.state = model.tone;
      element("state").textContent = model.title;
      element("description").textContent = model.description;
      for (const button of buttons) {
        button.disabled = !model.allowed.includes(button.dataset.action);
        button.hidden = !!status && status.lifecycle_state !== "ACTIVE" && ["mute", "unmute", "end"].includes(button.dataset.action) && !status.current_operation && !model.allowed.includes(button.dataset.action);
      }
      element("microphone").textContent = status ? (status.microphone_owned ? (status.muted ? "Otevřený · ztlumený" : "Otevřený") : "Uzavřený") : "Neznámý stav";
      element("cloud").textContent = !status ? "Neznámý stav" : status.cloud_ready ? "Připojený" : status.cloud_session_owned ? "Připravenost nepotvrzena" : "Odpojený";
      element("output").textContent = status ? (status.output_owned ? "Otevřený" : "Uzavřený") : "Neznámý stav";
      const elapsed = status && status.elapsed_session_seconds;
      element("elapsed").textContent = typeof elapsed === "number" && Number.isFinite(elapsed) ? `${Math.floor(elapsed / 60)}:${String(Math.floor(elapsed % 60)).padStart(2, "0")}` : "—";
      if (admin) {
        element("last-call").textContent = !status ? "Runtime status unavailable — poslední hovor nelze ověřit." : status.last_call ? JSON.stringify(status.last_call, null, 2) : "Zatím žádný záznam.";
        element("runtime-details").textContent = status ? JSON.stringify(status, null, 2) : "Runtime status unavailable";
      }
      if (status && status.last_command_outcome) {
        const outcome = status.last_command_outcome;
        element("feedback").textContent = outcome.status === "ACCEPTED" ? "Přijato. Čekám na potvrzení." : outcome.status === "SUCCEEDED" ? "Hotovo. Stav hovoru je uveden výše." : "Operace selhala nebo uzavření není potvrzené.";
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
      if (admin || submitting || !status || !view(status).allowed.includes(action)) return;
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
