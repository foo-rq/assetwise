// Each explanation belongs to its trigger's dialog so modal focus rules still apply
document.querySelectorAll("[data-help]").forEach((button, index) => {
  const popup = document.createElement("span");
  popup.id = `info-${index}`;
  popup.className = "info-popup";
  popup.setAttribute("popover", "manual");
  popup.setAttribute("role", "tooltip");
  popup.textContent = button.dataset.help;
  (button.closest("dialog") || document.body).append(popup);
  button.setAttribute("aria-describedby", popup.id);
  let pinned = false;
  let pointerType = "mouse";
  let leaveTimer;

  function close() {
    // Clear the pending pointer exit so reopening cannot inherit an old timeout
    clearTimeout(leaveTimer);
    pinned = false;
    popup.hidePopover();
  }

  function show() {
    clearTimeout(leaveTimer);
    popup.showPopover();
    const rect = button.getBoundingClientRect();
    // Measure after opening and clamp both edges, including help inside narrow dialogs
    const left = Math.max(12, Math.min(rect.left, innerWidth - popup.offsetWidth - 12));
    const below = rect.bottom + 4;
    const top = below + popup.offsetHeight < innerHeight - 12
      ? below : Math.max(12, rect.top - popup.offsetHeight - 4);
    popup.style.left = `${left}px`;
    popup.style.top = `${top}px`;
  }

  function leave() {
    // Allow the pointer to cross the small gap and read the popup without it vanishing
    leaveTimer = setTimeout(() => {
      if (pointerType === "mouse" || !pinned) close();
    }, 180);
  }

  button.addEventListener("pointerenter", event => {
    // Mouse help follows the pointer even after a click leaves focus on the icon
    pointerType = event.pointerType;
    if (pointerType === "mouse") show();
  });
  button.addEventListener("pointerleave", leave);
  button.addEventListener("focus", show);
  button.addEventListener("keydown", event => {
    if (event.key === "Enter" || event.key === " ") pointerType = "keyboard";
  });
  button.addEventListener("blur", () => { if (!pinned) close(); });
  popup.addEventListener("pointerenter", () => clearTimeout(leaveTimer));
  popup.addEventListener("pointerleave", leave);
  button.addEventListener("click", () => {
    // Touch and keyboard activation pin help while mouse help follows the pointer
    if (pinned) close();
    else { pinned = pointerType !== "mouse"; show(); }
  });
  document.addEventListener("pointerdown", event => {
    if (!button.contains(event.target) && !popup.contains(event.target)) close();
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && popup.matches(":popover-open")) {
      // Dismiss help first without also closing the portfolio dialog underneath
      event.preventDefault();
      event.stopImmediatePropagation();
      close();
    }
  }, true);
  button.closest("dialog")?.addEventListener("close", close);
  window.addEventListener("resize", close);
  document.addEventListener("scroll", close, true);
});
