/** The page itself: how the studio finds an element, and how it speaks.
 *
 * A toast for something that happened, a line in the top bar for what the
 * studio is doing, and one dialog that stands in for the browser's alert,
 * confirm and prompt -- so a question looks like the rest of the studio and
 * never blocks the page the way the built-in ones do.
 */

export const $ = (id) => document.getElementById(id);

let toastTimer;
export function toast(message) {
  $("toast").textContent = message;
  $("toast").classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("toast").classList.remove("show"), 3200);
}

export function setStatus(message, failure = false) {
  $("status").textContent = message;
  $("status").style.color = failure ? "var(--accent)" : "var(--success)";
}

let activeSystemDialog = null;
function closeSystemDialog(value) {
  if (!activeSystemDialog) return;
  $("systemDialog").classList.remove("open");
  activeSystemDialog.resolve(value);
  activeSystemDialog = null;
}

export function openSystemDialog({
  title,
  message = "",
  defaultValue = "",
  mode = "alert",
  confirmLabel = "OK",
  cancelLabel = "Cancel",
  danger = false
}) {
  return new Promise((resolve) => {
    if (activeSystemDialog) {
      closeSystemDialog(mode === "prompt" ? null : false);
    }
    activeSystemDialog = { resolve, mode };
    const input = $("systemDialogInput");
    const cancel = $("systemDialogCancel");
    const confirm = $("systemDialogConfirm");
    $("systemDialogTitle").textContent = title;
    $("systemDialogMessage").textContent = message;
    input.value = defaultValue;
    input.classList.toggle("hidden", mode !== "prompt");
    cancel.textContent = cancelLabel;
    cancel.classList.toggle("hidden", mode === "alert");
    confirm.textContent = confirmLabel;
    confirm.classList.toggle("danger", danger);
    $("systemDialog").classList.add("open");
    setTimeout(() => (mode === "prompt" ? input : confirm).focus(), 0);
  });
}

export function systemAlert(title, message = "") {
  return openSystemDialog({ title, message, mode: "alert", confirmLabel: "OK" });
}

export function systemConfirm(title, message, options = {}) {
  return openSystemDialog({
    title,
    message,
    mode: "confirm",
    confirmLabel: options.confirmLabel || "Confirm",
    cancelLabel: options.cancelLabel || "Cancel",
    danger: Boolean(options.danger)
  });
}

export function systemPrompt(title, defaultValue = "", message = "") {
  return openSystemDialog({
    title,
    message,
    defaultValue,
    mode: "prompt",
    confirmLabel: "Save",
    cancelLabel: "Cancel"
  });
}

/** Close whatever question is open, the way Escape should.
 *
 * Answers whether there was one, so the key handler that calls it knows
 * whether Escape has already been spent.
 */
export function dismissSystemDialog() {
  if (!activeSystemDialog) return false;
  closeSystemDialog(activeSystemDialog.mode === "prompt" ? null : false);
  return true;
}

/** The dialog's own buttons, wired to it.
 *
 * Cancel, confirm, a click on the backdrop and Enter in the field: the four
 * ways out of a question, all of them the dialog's business rather than the
 * studio's.
 */
export function wireSystemDialog() {
  const dismissed = () => (activeSystemDialog && activeSystemDialog.mode === "prompt" ? null : false);
  $("systemDialogCancel").onclick = () => closeSystemDialog(dismissed());
  $("systemDialogConfirm").onclick = () => {
    if (!activeSystemDialog) return;
    closeSystemDialog(activeSystemDialog.mode === "prompt" ? $("systemDialogInput").value : true);
  };
  $("systemDialog").onclick = (event) => {
    if (event.target === $("systemDialog")) closeSystemDialog(dismissed());
  };
  $("systemDialogInput").onkeydown = (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    closeSystemDialog($("systemDialogInput").value);
  };
}
