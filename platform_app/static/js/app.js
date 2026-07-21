(function () {
  const forms = document.querySelectorAll("[data-parse-upload-form]");
  const statusHost = document.querySelector("[data-parse-status]");
  if (!forms.length || !statusHost) {
    return;
  }

  const titleNode = statusHost.querySelector("[data-parse-status-title]");
  const messageNode = statusHost.querySelector("[data-parse-status-message]");
  const barNode = statusHost.querySelector("[data-parse-status-bar]");
  let timer = null;
  let progress = 12;

  function showStatus(kind, title, message) {
    statusHost.classList.remove("is-success", "is-error", "is-running");
    statusHost.classList.add("is-visible", `is-${kind}`);
    statusHost.setAttribute("aria-hidden", "false");
    titleNode.textContent = title;
    messageNode.textContent = message;
  }

  function startProgress(title) {
    progress = 12;
    if (barNode) {
      barNode.style.width = `${progress}%`;
    }
    showStatus("running", title || "正在解析招标文件", "正在上传文件并调用解析服务，请保持页面打开。");
    window.clearInterval(timer);
    timer = window.setInterval(() => {
      progress = Math.min(progress + Math.max(1, Math.round((88 - progress) * 0.12)), 88);
      if (barNode) {
        barNode.style.width = `${progress}%`;
      }
      if (progress > 42 && messageNode) {
        messageNode.textContent = "正在读取 Word/PDF 文本、提取关键条款并生成待确认结果。";
      }
      if (progress > 70 && messageNode) {
        messageNode.textContent = "正在整理原文依据和字段来源，马上生成确认页。";
      }
    }, 900);
  }

  function finishProgress(kind, title, message) {
    window.clearInterval(timer);
    timer = null;
    if (barNode) {
      barNode.style.width = kind === "success" ? "100%" : "92%";
    }
    showStatus(kind, title, message);
  }

  function restoreForm(form) {
    const button = form.querySelector("button[type='submit']");
    if (button) {
      button.disabled = false;
      if (button.dataset.originalText) {
        button.textContent = button.dataset.originalText;
      }
    }
  }

  function lockForm(form) {
    const button = form.querySelector("button[type='submit']");
    if (button) {
      button.dataset.originalText = button.textContent;
      button.disabled = true;
      button.textContent = "解析中...";
    }
  }

  async function parseUpload(form) {
    const label = form.dataset.parseLabel || "AI 正在解析招标文件";
    lockForm(form);
    startProgress(label);

    try {
      const response = await fetch(form.action, {
        method: form.method || "POST",
        body: new FormData(form),
        headers: {
          "Accept": "application/json",
          "X-Requested-With": "fetch",
        },
      });

      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) {
        finishProgress("success", "解析请求已提交", "页面即将跳转到系统返回结果。");
        window.location.href = response.url || form.action;
        return;
      }

      const result = await response.json();
      if (!response.ok || !result.ok) {
        const message = result.error || result.message || `解析失败，服务返回 HTTP ${response.status}`;
        finishProgress("error", "解析失败", message);
        restoreForm(form);
        return;
      }

      finishProgress("success", "解析成功", result.message || "已生成 AI 解析待确认结果，正在打开确认页。");
      window.setTimeout(() => {
        window.location.href = result.redirect_url || response.url || "/workspace/reviews";
      }, 700);
    } catch (error) {
      finishProgress("error", "解析失败", error && error.message ? error.message : "网络异常或服务暂时不可用，请稍后重试。");
      restoreForm(form);
    }
  }

  forms.forEach((form) => {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      parseUpload(form);
    });
  });
})();

(() => {
  if (window.lucide) {
    window.lucide.createIcons({ attrs: { "stroke-width": 1.8 } });
  }

  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const container = button.closest(".project-hero")?.nextElementSibling;
      document.querySelectorAll("[data-tab]").forEach((item) => item.classList.remove("active"));
      container?.querySelectorAll(".tab-pane").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      document.getElementById(button.dataset.tab)?.classList.add("active");
    });
  });

  document.querySelectorAll("[data-settings-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-settings-tab]").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll(".settings-pane").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      document.getElementById(`settings-${button.dataset.settingsTab}`)?.classList.add("active");
    });
  });
  const requestedSettingsTab = window.location.hash.replace("#", "");
  if (requestedSettingsTab) {
    document.querySelector(`[data-settings-tab="${requestedSettingsTab}"]`)?.click();
  }

  document.querySelectorAll("[data-dialog-open]").forEach((button) => {
    button.addEventListener("click", () => document.getElementById(button.dataset.dialogOpen)?.showModal());
  });
  document.querySelectorAll("[data-dialog-close]").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog")?.close());
  });

  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const value = document.getElementById(button.dataset.copyTarget)?.textContent || "";
      await navigator.clipboard.writeText(value.trim());
      const original = button.textContent;
      button.textContent = "已复制";
      window.setTimeout(() => { button.textContent = original; }, 1500);
    });
  });

  document.querySelector("[data-print-project]")?.addEventListener("click", () => window.print());
  document.querySelectorAll("form:not([data-parse-upload-form])").forEach((form) => {
    form.addEventListener("submit", () => {
      form.setAttribute("aria-busy", "true");
      const submitter = form.querySelector("button[type='submit']");
      if (!submitter) return;
      window.requestAnimationFrame(() => {
        submitter.disabled = true;
        submitter.dataset.originalLabel = submitter.textContent.trim();
        submitter.textContent = "处理中…";
      });
    });
  });

  if (window.location.hash === "#manual-create") {
    document.getElementById("manual-create")?.showModal();
  }

  const editor = document.querySelector("[data-dynamic-editor]");
  if (editor) {
    const form = editor.closest("form");
    const output = editor.querySelector("textarea[name='content_json']");
    const jsonSource = editor.querySelector("[data-json-source]");
    const list = editor.querySelector("[data-section-list]");
    const preview = editor.querySelector("[data-editor-preview]");
    const visualPane = editor.querySelector("[data-editor-visual]");
    const jsonPane = editor.querySelector("[data-editor-json]");
    const error = editor.querySelector("[data-editor-error]");
    const blockTypes = ["field", "status", "text", "list", "table", "timeline", "checklist", "callout", "files", "divider"];
    let dirty = false;
    let content;

    const uid = (prefix) => `${prefix}-${Math.random().toString(36).slice(2, 9)}`;
    const text = (tag, value, className) => {
      const node = document.createElement(tag);
      node.textContent = value == null ? "" : String(value);
      if (className) node.className = className;
      return node;
    };
    const iconButton = (icon, label, action) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "builder-icon-btn";
      button.dataset.action = action;
      button.setAttribute("aria-label", label);
      button.title = label;
      button.innerHTML = `<i data-lucide="${icon}"></i>`;
      return button;
    };
    const input = (value, label, onInput, kind = "input") => {
      const wrap = document.createElement("label");
      wrap.className = "builder-field";
      wrap.append(text("span", label));
      const control = document.createElement(kind);
      control.value = value == null ? "" : String(value);
      control.addEventListener("input", () => { onInput(control.value); changed(); });
      wrap.append(control);
      return wrap;
    };
    const normalizeBlock = (type) => {
      const base = { id: uid(type), type };
      if (type === "field") return { ...base, label: "新字段", value: "", width: "full" };
      if (type === "status") return { ...base, label: "状态", value: "待处理", tone: "info" };
      if (type === "text") return { ...base, title: "正文", content: "" };
      if (type === "list") return { ...base, title: "列表", items: ["新条目"] };
      if (type === "table") return { ...base, title: "表格", columns: ["项目", "内容"], rows: [["", ""]] };
      if (type === "timeline") return { ...base, items: [{ label: "新节点", at: "", status: "待完成", tone: "info" }] };
      if (type === "checklist") return { ...base, items: [{ label: "新事项", done: false, note: "" }] };
      if (type === "callout") return { ...base, title: "提示", content: "", tone: "info" };
      if (type === "files") return { ...base, title: "附件", items: [] };
      return base;
    };
    const changed = () => {
      dirty = true;
      output.value = JSON.stringify(content, null, 2);
      jsonSource.value = output.value;
      renderPreview();
      error.hidden = true;
    };
    const move = (items, index, delta) => {
      const target = index + delta;
      if (target < 0 || target >= items.length) return;
      [items[index], items[target]] = [items[target], items[index]];
      changed(); renderBuilder();
    };
    const select = (value, options, onChange) => {
      const node = document.createElement("select");
      options.forEach(([key, label]) => { const option = new Option(label, key, false, key === value); node.add(option); });
      node.addEventListener("change", () => { onChange(node.value); changed(); });
      return node;
    };
    const selectField = (value, label, options, onChange) => {
      const wrap = document.createElement("label"); wrap.className = "builder-field";
      wrap.append(text("span", label), select(value, options, onChange));
      return wrap;
    };
    function renderBuilder() {
      list.replaceChildren();
      content.sections.forEach((section, sectionIndex) => {
        const card = document.createElement("article"); card.className = "section-builder";
        const head = document.createElement("header");
        const titleInput = document.createElement("input"); titleInput.value = section.title || ""; titleInput.setAttribute("aria-label", "标签页名称");
        titleInput.addEventListener("input", () => { section.title = titleInput.value; changed(); });
        head.append(titleInput, iconButton("arrow-up", "上移标签页", "up"), iconButton("arrow-down", "下移标签页", "down"), iconButton("trash-2", "删除标签页", "remove"));
        head.addEventListener("click", (event) => {
          const action = event.target.closest("button")?.dataset.action;
          if (action === "up") move(content.sections, sectionIndex, -1);
          if (action === "down") move(content.sections, sectionIndex, 1);
          if (action === "remove" && window.confirm(`删除标签页“${section.title || "未命名"}”？`)) { content.sections.splice(sectionIndex, 1); changed(); renderBuilder(); }
        });
        card.append(head);
        const meta = document.createElement("div"); meta.className = "section-meta";
        meta.append(input(section.description || "", "标签说明", value => { section.description = value; }), select(section.priority || "normal", [["normal","普通"],["important","重要"],["urgent","紧急"]], value => { section.priority = value; }));
        card.append(meta);
        const blocks = document.createElement("div"); blocks.className = "block-builder-list";
        (section.blocks || []).forEach((block, blockIndex) => blocks.append(renderBlock(block, section.blocks, blockIndex)));
        card.append(blocks);
        const add = document.createElement("div"); add.className = "add-block-row";
        const typeSelect = select("field", blockTypes.map(type => [type, type]), () => {});
        const addButton = text("button", "添加内容块", "secondary-btn"); addButton.type = "button";
        addButton.addEventListener("click", () => { section.blocks.push(normalizeBlock(typeSelect.value)); changed(); renderBuilder(); });
        add.append(typeSelect, addButton); card.append(add); list.append(card);
      });
      if (!content.sections.length) list.append(text("div", "暂无标签页，请先添加。", "empty-state compact"));
      window.lucide?.createIcons({ attrs: { "stroke-width": 1.8 } });
    }
    function renderBlock(block, blocks, index) {
      const card = document.createElement("article"); card.className = "block-builder";
      const head = document.createElement("header"); head.append(text("strong", block.type), iconButton("arrow-up", "上移内容块", "up"), iconButton("arrow-down", "下移内容块", "down"), iconButton("trash-2", "删除内容块", "remove"));
      head.addEventListener("click", (event) => { const action = event.target.closest("button")?.dataset.action; if (action === "up") move(blocks,index,-1); if (action === "down") move(blocks,index,1); if (action === "remove") { blocks.splice(index,1); changed(); renderBuilder(); } });
      card.append(head);
      const fields = document.createElement("div"); fields.className = "block-fields";
      if (["field","status"].includes(block.type)) fields.append(input(block.label, "标签", value => { block.label=value; }), input(block.value, "内容", value => { block.value=value; }));
      fields.append(selectField(block.width || "full", "宽度", [["full","整行"],["half","半行"],["third","三分之一"]], value => { block.width=value; }));
      if (block.type === "field") fields.append(selectField(block.semantic || "text", "字段语义", [["text","文本"],["date","日期"],["datetime","日期时间"],["amount","金额"],["phone","电话"],["email","邮箱"],["url","链接"]], value => { block.semantic=value; }));
      if (block.type === "status") fields.append(selectField(block.tone || "neutral", "状态语义", [["neutral","中性"],["info","信息"],["success","成功"],["warning","警告"],["danger","危险"]], value => { block.tone=value; }));
      if (["text","callout"].includes(block.type)) fields.append(input(block.title || "", "标题", value => { block.title=value; }), input(block.content || "", "正文", value => { block.content=value; }, "textarea"));
      if (block.type === "callout") fields.append(selectField(block.tone || "info", "提示语义", [["info","信息"],["success","成功"],["warning","警告"],["danger","危险"]], value => { block.tone=value; }));
      if (["list","files"].includes(block.type)) fields.append(input((block.items || []).map(item => typeof item === "string" ? item : item.label || item.name || "").join("\n"), "每行一项", value => { block.items=value.split("\n").filter(Boolean); }, "textarea"));
      if (block.type === "files") { fields.lastElementChild?.remove(); fields.append(input(JSON.stringify(block.items || [], null, 2), "附件数组 JSON（name、url）", value => { try { block.items=JSON.parse(value); } catch (_) {} }, "textarea")); }
      if (["table","timeline","checklist"].includes(block.type)) fields.append(input(JSON.stringify(block.type === "table" ? {columns:block.columns,rows:block.rows} : block.items, null, 2), "结构化数据 JSON", value => { try { const parsed=JSON.parse(value); if(block.type==="table"){block.columns=parsed.columns;block.rows=parsed.rows;}else{block.items=parsed;} } catch (_) {} }, "textarea"));
      if (block.type === "divider") fields.append(text("span", "分隔线没有可编辑内容。", "muted"));
      card.append(fields); return card;
    }
    function renderPreview() {
      preview.replaceChildren();
      content.sections.forEach(section => {
        const sectionNode = document.createElement("section"); sectionNode.className = "preview-section";
        sectionNode.append(text("h3", section.title || "未命名标签页"));
        if (section.description) sectionNode.append(text("p", section.description, "muted"));
        (section.blocks || []).forEach(block => {
          if (block.type === "divider") { sectionNode.append(document.createElement("hr")); return; }
          const item = document.createElement("article"); item.className = `preview-block preview-${block.type}`;
          item.append(text("small", block.label || block.title || block.type));
          if (["field","status"].includes(block.type)) item.append(text("strong", block.value || "-"));
          else if (["text","callout"].includes(block.type)) item.append(text("p", block.content || ""));
          else if (block.type === "table") item.append(text("p", `${(block.columns || []).length} 列 · ${(block.rows || []).length} 行`));
          else item.append(text("p", `${(block.items || []).length} 项`));
          sectionNode.append(item);
        });
        preview.append(sectionNode);
      });
      if (!content.sections.length) preview.append(text("div", "添加标签页后将在这里预览。", "empty-state compact"));
    }
    try { content = JSON.parse(output.value); } catch (_) { content = { sections: [] }; }
    if (!Array.isArray(content.sections)) content.sections = [];
    jsonSource.value = JSON.stringify(content, null, 2);
    editor.querySelector("[data-add-section]").addEventListener("click", () => { content.sections.push({ id: uid("section"), title: "新标签页", description: "", priority: "normal", blocks: [] }); changed(); renderBuilder(); });
    editor.querySelectorAll("[data-editor-mode]").forEach(button => button.addEventListener("click", () => {
      if (button.dataset.editorMode === "visual") {
        try { content = JSON.parse(jsonSource.value); if (!Array.isArray(content.sections)) throw new Error("sections 必须是数组"); output.value = JSON.stringify(content, null, 2); error.hidden = true; renderBuilder(); renderPreview(); }
        catch (exception) { error.textContent = `JSON 无法切换到可视化模式：${exception.message}`; error.hidden = false; return; }
      }
      visualPane.hidden = button.dataset.editorMode !== "visual"; jsonPane.hidden = button.dataset.editorMode !== "json";
    }));
    jsonSource.addEventListener("input", () => { output.value = jsonSource.value; dirty = true; });
    form.addEventListener("submit", () => { output.value = jsonPane.hidden ? JSON.stringify(content) : jsonSource.value; dirty = false; });
    window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
    renderBuilder(); renderPreview();
  }
})();
