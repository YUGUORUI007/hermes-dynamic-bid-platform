(() => {
  function showNotice(message, isError) {
    const notice = document.createElement("div");
    notice.className = `notice ${isError ? "error" : "success"} status-update-notice`;
    notice.setAttribute("role", isError ? "alert" : "status");
    notice.setAttribute("aria-live", "polite");
    const icon = document.createElement("i");
    icon.setAttribute("data-lucide", isError ? "circle-alert" : "circle-check");
    const text = document.createElement("span");
    text.textContent = message;
    notice.append(icon, text);
    document.body.append(notice);
    window.lucide?.createIcons({ attrs: { "stroke-width": 1.8 } });
    window.setTimeout(() => notice.remove(), 3000);
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".status-select").forEach((select) => {
      select.addEventListener("pointerdown", (event) => event.stopPropagation());
      select.addEventListener("click", (event) => event.stopPropagation());
      select.addEventListener("change", async (event) => {
        event.stopPropagation();
        const previous = select.dataset.current || "";
        const status = select.value;
        select.disabled = true;
        try {
          const response = await fetch(`/api/projects/${encodeURIComponent(select.dataset.projectId)}/status`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json", "Accept": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({ status }),
          });
          const payload = await response.json().catch(() => ({}));
          if (!response.ok || !payload.ok) throw new Error(payload.detail || "状态更新失败，请稍后重试。");
          select.dataset.current = payload.status;
          select.className = `status-select ${select.options[select.selectedIndex]?.dataset.tone || ""}`.trim();
          showNotice(`项目状态已更新为：${payload.status_label}`);
        } catch (error) {
          select.value = previous;
          showNotice(error instanceof Error ? error.message : "状态更新失败，请稍后重试。", true);
        } finally {
          select.disabled = false;
        }
      });
    });
  });
})();
