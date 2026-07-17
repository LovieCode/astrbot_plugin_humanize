(function initializeHumanizeUi(global) {
  "use strict";

  const Core = global.HumanizeCore;
  if (!Core) {
    console.error("[HumanizeUi] HumanizeCore not loaded; UI library disabled.");
    return;
  }

  /**
   * Build a button element with variant, size, icon and label.
   * @param {object} opts {label, variant, size, icon, onClick, disabled, block, type, title}
   * @returns {HTMLButtonElement}
   */
  function createButton(opts) {
    const o = opts || {};
    const btn = document.createElement("button");
    btn.type = o.type || "button";
    btn.className = "btn";
    if (o.variant === "primary") btn.classList.add("btn-primary");
    else if (o.variant === "ghost") btn.classList.add("btn-ghost");
    else if (o.variant === "outline") btn.classList.add("btn-outline");
    else if (o.variant === "danger") btn.classList.add("btn-danger");
    if (o.size === "sm") btn.classList.add("btn-sm");
    else if (o.size === "lg") btn.classList.add("btn-lg");
    if (o.block) btn.classList.add("btn-block");
    if (o.icon && !o.label) btn.classList.add("btn-icon-only");
    if (o.disabled) btn.disabled = true;
    if (o.title) btn.title = o.title;
    if (o.icon) {
      const ic = Core.icon(o.icon);
      ic.classList.add("btn-icon");
      btn.append(ic);
    }
    if (o.label) {
      const span = document.createElement("span");
      span.textContent = String(o.label);
      btn.append(span);
    }
    if (typeof o.onClick === "function") {
      btn.addEventListener("click", (ev) => o.onClick(ev, btn));
    }
    return btn;
  }

  /**
   * Build a labeled field wrapper for inputs/textareas/selects.
   * @param {object} opts {label, required, hint, error, control, htmlFor}
   * @returns {HTMLDivElement} .field
   */
  function createField(opts) {
    const o = opts || {};
    const wrap = document.createElement("div");
    wrap.className = "field";
    if (o.label) {
      const lab = document.createElement("label");
      lab.className = "field-label";
      lab.textContent = o.label;
      if (o.htmlFor) lab.setAttribute("for", o.htmlFor);
      if (o.required) {
        const req = document.createElement("span");
        req.className = "field-required";
        req.textContent = "*";
        req.setAttribute("aria-hidden", "true");
        lab.append(req);
      }
      wrap.append(lab);
    }
    if (o.control) wrap.append(o.control);
    if (o.hint) {
      const hint = document.createElement("div");
      hint.className = "field-hint";
      hint.textContent = o.hint;
      wrap.append(hint);
    }
    if (o.error) {
      const err = document.createElement("div");
      err.className = "field-error";
      err.textContent = o.error;
      wrap.append(err);
    }
    return wrap;
  }

  /**
   * Build an input element with common options.
   * @param {object} opts {type, value, placeholder, name, id, mono, sm, onChange, onInput}
   * @returns {HTMLInputElement}
   */
  function createInput(opts) {
    const o = opts || {};
    const inp = document.createElement("input");
    inp.type = o.type || "text";
    inp.className = "input";
    if (o.mono) inp.classList.add("input-code");
    if (o.sm) inp.classList.add("input-sm");
    if (o.value !== undefined) inp.value = String(o.value ?? "");
    if (o.placeholder) inp.placeholder = String(o.placeholder);
    if (o.name) inp.name = o.name;
    if (o.id) inp.id = o.id;
    if (o.disabled) inp.disabled = true;
    if (o.readonly) inp.readOnly = true;
    if (o.maxlength) inp.maxLength = o.maxlength;
    if (typeof o.onChange === "function") {
      inp.addEventListener("change", (ev) => o.onChange(ev.target.value, ev));
    }
    if (typeof o.onInput === "function") {
      inp.addEventListener("input", (ev) => o.onInput(ev.target.value, ev));
    }
    return inp;
  }

  /**
   * Build a textarea element with autosize-friendly options.
   * @param {object} opts {value, placeholder, rows, name, id, mono, sm, onChange, onInput}
   * @returns {HTMLTextAreaElement}
   */
  function createTextarea(opts) {
    const o = opts || {};
    const ta = document.createElement("textarea");
    ta.className = "textarea";
    if (o.mono) ta.classList.add("textarea-code");
    if (o.sm) ta.classList.add("textarea-sm");
    if (o.rows) ta.rows = o.rows;
    if (o.value !== undefined) ta.value = String(o.value ?? "");
    if (o.placeholder) ta.placeholder = String(o.placeholder);
    if (o.name) ta.name = o.name;
    if (o.id) ta.id = o.id;
    if (o.disabled) ta.disabled = true;
    if (o.readonly) ta.readOnly = true;
    if (o.maxlength) ta.maxLength = o.maxlength;
    if (typeof o.onChange === "function") {
      ta.addEventListener("change", (ev) => o.onChange(ev.target.value, ev));
    }
    if (typeof o.onInput === "function") {
      ta.addEventListener("input", (ev) => o.onInput(ev.target.value, ev));
    }
    return ta;
  }

  /**
   * Build a select element with options [{value, label, disabled}].
   * @param {object} opts {value, options, name, id, sm, onChange}
   * @returns {HTMLSelectElement}
   */
  function createSelect(opts) {
    const o = opts || {};
    const sel = document.createElement("select");
    sel.className = "select";
    if (o.sm) sel.classList.add("select-sm");
    if (o.name) sel.name = o.name;
    if (o.id) sel.id = o.id;
    if (o.disabled) sel.disabled = true;
    (o.options || []).forEach((opt) => {
      const item = opt || {};
      const node = document.createElement("option");
      node.value = String(item.value ?? "");
      node.textContent = String(item.label ?? "");
      if (item.disabled) node.disabled = true;
      if (item.value === o.value) node.selected = true;
      sel.append(node);
    });
    if (typeof o.onChange === "function") {
      sel.addEventListener("change", (ev) => o.onChange(ev.target.value, ev));
    }
    return sel;
  }

  /**
   * Build a badge element with variant.
   * @param {string} text
   * @param {string} [variant] pink|success|warning|danger|info
   * @param {string} [iconName]
   * @returns {HTMLSpanElement}
   */
  function createBadge(text, variant, iconName) {
    const span = document.createElement("span");
    span.className = "badge";
    if (variant) span.classList.add(`badge-${variant}`);
    if (iconName) {
      const dot = document.createElement("span");
      dot.className = "badge-dot";
      dot.setAttribute("aria-hidden", "true");
      span.append(dot);
    }
    span.append(String(text ?? ""));
    return span;
  }

  /**
   * Build a chip element (smaller than badge, used for tags/keywords).
   * @param {string} text
   * @param {string} [iconName]
   * @returns {HTMLSpanElement}
   */
  function createChip(text, iconName) {
    const span = document.createElement("span");
    span.className = "chip";
    if (iconName) {
      const ic = Core.icon(iconName);
      ic.classList.add("chip-icon");
      span.append(ic);
    }
    span.append(String(text ?? ""));
    return span;
  }

  /**
   * Build a metric card.
   * @param {object} opts {label, value, delta, deltaDir, icon, hint}
   * @returns {HTMLDivElement}
   */
  function createMetric(opts) {
    const o = opts || {};
    const card = document.createElement("div");
    card.className = "metric";
    const lab = document.createElement("div");
    lab.className = "metric-label";
    if (o.icon) {
      const ic = Core.icon(o.icon);
      ic.classList.add("metric-icon");
      lab.append(ic);
    }
    const labText = document.createElement("span");
    labText.textContent = String(o.label ?? "");
    lab.append(labText);
    card.append(lab);
    const val = document.createElement("div");
    val.className = "metric-value";
    if (o.small) val.classList.add("metric-value-sm");
    val.textContent = String(o.value ?? "--");
    card.append(val);
    if (o.delta !== undefined && o.delta !== null && o.delta !== "") {
      const d = document.createElement("div");
      d.className = "metric-delta";
      if (o.deltaDir === "up") d.classList.add("metric-delta-up");
      else if (o.deltaDir === "down") d.classList.add("metric-delta-down");
      d.textContent = String(o.delta);
      card.append(d);
    } else if (o.hint) {
      const h = document.createElement("div");
      h.className = "metric-delta";
      h.textContent = String(o.hint);
      card.append(h);
    }
    return card;
  }

  /**
   * Build a tabs bar with change callback.
   * @param {Array<{key, label, count?, disabled?}>} tabs
   * @param {string} activeKey
   * @param {(key:string, ev:Event)=>void} onChange
   * @returns {HTMLElement} .tabs
   */
  function createTabs(tabs, activeKey, onChange) {
    const bar = document.createElement("div");
    bar.className = "tabs";
    bar.setAttribute("role", "tablist");
    (tabs || []).forEach((t) => {
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = "tab";
      tab.setAttribute("role", "tab");
      tab.dataset.key = String(t.key);
      if (t.disabled) {
        tab.classList.add("tab-disabled");
        tab.setAttribute("aria-disabled", "true");
        tab.disabled = true;
      }
      if (t.key === activeKey) {
        tab.setAttribute("aria-selected", "true");
        tab.setAttribute("aria-current", "page");
      }
      const lab = document.createElement("span");
      lab.textContent = String(t.label ?? t.key);
      tab.append(lab);
      if (t.count !== undefined && t.count !== null) {
        const c = document.createElement("span");
        c.className = "tab-count";
        c.textContent = String(t.count);
        tab.append(c);
      }
      if (typeof onChange === "function" && !t.disabled) {
        tab.addEventListener("click", (ev) => onChange(t.key, ev));
      }
      bar.append(tab);
    });
    return bar;
  }

  /**
   * Update the active tab in a tabs bar (no re-render needed).
   * @param {HTMLElement} bar
   * @param {string} activeKey
   */
  function setActiveTab(bar, activeKey) {
    if (!bar) return;
    bar.querySelectorAll(".tab").forEach((tab) => {
      const key = tab.dataset.key;
      if (key === activeKey) {
        tab.setAttribute("aria-selected", "true");
        tab.setAttribute("aria-current", "page");
      } else {
        tab.removeAttribute("aria-selected");
        tab.removeAttribute("aria-current");
      }
    });
  }

  /**
   * Build pagination controls.
   * @param {object} opts {page, pageSize, total, maxButtons, onChange}
   * @returns {HTMLElement} .pagination
   */
  function createPagination(opts) {
    const o = opts || {};
    const page = Math.max(1, Core.numberValue(o.page, 1));
    const pageSize = Math.max(1, Core.numberValue(o.pageSize, 20));
    const total = Math.max(0, Core.numberValue(o.total, 0));
    const maxButtons = Math.max(3, Core.numberValue(o.maxButtons, 5));
    const totalPages = Math.max(1, Math.ceil(total / pageSize));

    const root = document.createElement("div");
    root.className = "pagination";

    const info = document.createElement("div");
    info.className = "pagination-info";
    if (total === 0) {
      info.textContent = "暂无数据";
    } else {
      const from = (page - 1) * pageSize + 1;
      const to = Math.min(page * pageSize, total);
      info.textContent = `第 ${from}-${to} 条，共 ${total} 条`;
    }
    root.append(info);

    const ctrls = document.createElement("div");
    ctrls.className = "pagination-controls";

    const prev = document.createElement("button");
    prev.type = "button";
    prev.className = "pagination-btn";
    prev.append(Core.icon("chevron-right"));
    prev.querySelector("svg")?.style.setProperty("transform", "rotate(180deg)");
    prev.title = "上一页";
    prev.disabled = page <= 1;
    if (page > 1 && typeof o.onChange === "function") {
      prev.addEventListener("click", () => o.onChange(page - 1));
    }
    ctrls.append(prev);

    let startPage = Math.max(1, page - Math.floor(maxButtons / 2));
    let endPage = Math.min(totalPages, startPage + maxButtons - 1);
    if (endPage - startPage + 1 < maxButtons) {
      startPage = Math.max(1, endPage - maxButtons + 1);
    }
    if (startPage > 1) {
      const first = document.createElement("button");
      first.type = "button";
      first.className = "pagination-btn";
      first.textContent = "1";
      if (typeof o.onChange === "function") {
        first.addEventListener("click", () => o.onChange(1));
      }
      ctrls.append(first);
      if (startPage > 2) {
        const ell = document.createElement("span");
        ell.className = "pagination-btn";
        ell.textContent = "…";
        ell.style.background = "transparent";
        ell.style.border = "none";
        ell.style.cursor = "default";
        ctrls.append(ell);
      }
    }
    for (let p = startPage; p <= endPage; p++) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "pagination-btn";
      btn.textContent = String(p);
      if (p === page) btn.setAttribute("aria-current", "page");
      if (typeof o.onChange === "function") {
        btn.addEventListener("click", () => o.onChange(p));
      }
      ctrls.append(btn);
    }
    if (endPage < totalPages) {
      if (endPage < totalPages - 1) {
        const ell = document.createElement("span");
        ell.className = "pagination-btn";
        ell.textContent = "…";
        ell.style.background = "transparent";
        ell.style.border = "none";
        ell.style.cursor = "default";
        ctrls.append(ell);
      }
      const last = document.createElement("button");
      last.type = "button";
      last.className = "pagination-btn";
      last.textContent = String(totalPages);
      if (typeof o.onChange === "function") {
        last.addEventListener("click", () => o.onChange(totalPages));
      }
      ctrls.append(last);
    }

    const next = document.createElement("button");
    next.type = "button";
    next.className = "pagination-btn";
    next.append(Core.icon("chevron-right"));
    next.title = "下一页";
    next.disabled = page >= totalPages;
    if (page < totalPages && typeof o.onChange === "function") {
      next.addEventListener("click", () => o.onChange(page + 1));
    }
    ctrls.append(next);

    root.append(ctrls);
    return root;
  }

  /**
   * Build an empty state placeholder.
   * @param {object} opts {title, message, icon, actions[]}
   * @returns {HTMLElement} .empty-state
   */
  function createEmptyState(opts) {
    const o = opts || {};
    const root = document.createElement("div");
    root.className = "empty-state";
    // 装饰点 3：empty-state 樱花 SVG（48×48，灰粉色，5 瓣描边）
    const sakura = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    sakura.setAttribute("class", "empty-state-icon");
    sakura.setAttribute("viewBox", "0 0 48 48");
    sakura.setAttribute("width", "48");
    sakura.setAttribute("height", "48");
    sakura.setAttribute("fill", "none");
    sakura.setAttribute("stroke", "currentColor");
    sakura.setAttribute("stroke-width", "1.2");
    sakura.setAttribute("stroke-linecap", "round");
    sakura.setAttribute("stroke-linejoin", "round");
    sakura.setAttribute("aria-hidden", "true");
    // 5 瓣樱花：每瓣是一个椭圆轮廓，绕中心 72° 旋转
    for (let i = 0; i < 5; i++) {
      const petal = document.createElementNS("http://www.w3.org/2000/svg", "ellipse");
      petal.setAttribute("cx", "24");
      petal.setAttribute("cy", "10");
      petal.setAttribute("rx", "5");
      petal.setAttribute("ry", "9");
      petal.setAttribute("transform", `rotate(${i * 72} 24 24)`);
      sakura.append(petal);
    }
    const center = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    center.setAttribute("cx", "24");
    center.setAttribute("cy", "24");
    center.setAttribute("r", "2");
    sakura.append(center);
    root.append(sakura);
    if (o.title) {
      const t = document.createElement("div");
      t.className = "empty-state-title";
      t.textContent = String(o.title);
      root.append(t);
    }
    if (o.message) {
      const m = document.createElement("div");
      m.className = "empty-state-message";
      m.textContent = String(o.message);
      root.append(m);
    }
    if (Array.isArray(o.actions) && o.actions.length) {
      const actions = document.createElement("div");
      actions.className = "empty-state-actions";
      o.actions.forEach((action) => {
        if (typeof action === "function") {
          actions.append(action());
        } else if (action instanceof HTMLElement) {
          actions.append(action);
        }
      });
      root.append(actions);
    }
    return root;
  }

  /**
   * Build an alert/notice box.
   * @param {object} opts {variant, title, message, icon}
   * @returns {HTMLElement} .alert
   */
  function createAlert(opts) {
    const o = opts || {};
    const root = document.createElement("div");
    root.className = "alert";
    if (o.variant) root.classList.add(`alert-${o.variant}`);
    const ic = Core.icon(o.icon || (o.variant === "success" ? "check"
      : o.variant === "warning" ? "circle-alert"
      : o.variant === "danger" ? "ban"
      : o.variant === "info" ? "circle-alert"
      : "circle-alert"));
    ic.classList.add("alert-icon");
    root.append(ic);
    const content = document.createElement("div");
    content.className = "alert-content";
    if (o.title) {
      const t = document.createElement("div");
      t.className = "alert-title";
      t.textContent = String(o.title);
      content.append(t);
    }
    if (o.message) {
      const m = document.createElement("div");
      m.className = "alert-message";
      m.textContent = String(o.message);
      content.append(m);
    }
    root.append(content);
    return root;
  }

  /**
   * Build a panel container with header/body/footer slots.
   * @param {object} opts {title, subtitle, icon, actions[], body, footer, bodyTight, bodyFlush}
   * @returns {HTMLElement} .panel
   */
  function createPanel(opts) {
    const o = opts || {};
    const root = document.createElement("section");
    root.className = "panel";
    if (o.title || o.actions) {
      const head = document.createElement("div");
      head.className = "panel-header";
      const titleGroup = document.createElement("div");
      titleGroup.style.display = "flex";
      titleGroup.style.flexDirection = "column";
      titleGroup.style.gap = "2px";
      const title = document.createElement("div");
      title.className = "panel-title";
      if (o.icon) {
        const ic = Core.icon(o.icon);
        ic.classList.add("panel-title-icon");
        title.append(ic);
      }
      const tlab = document.createElement("span");
      tlab.textContent = String(o.title ?? "");
      title.append(tlab);
      titleGroup.append(title);
      if (o.subtitle) {
        const sub = document.createElement("div");
        sub.className = "panel-subtitle";
        sub.textContent = String(o.subtitle);
        titleGroup.append(sub);
      }
      head.append(titleGroup);
      if (Array.isArray(o.actions) && o.actions.length) {
        const acts = document.createElement("div");
        acts.className = "panel-actions";
        o.actions.forEach((action) => {
          if (typeof action === "function") {
            acts.append(action());
          } else if (action instanceof HTMLElement) {
            acts.append(action);
          }
        });
        head.append(acts);
      }
      root.append(head);
    }
    if (o.body !== undefined) {
      const body = document.createElement("div");
      body.className = "panel-body";
      if (o.bodyTight) body.classList.add("panel-body-tight");
      if (o.bodyFlush) body.classList.add("panel-body-flush");
      if (typeof o.body === "function") {
        const result = o.body();
        if (result instanceof HTMLElement) body.append(result);
        else if (Array.isArray(result)) result.forEach((n) => body.append(n));
      } else if (o.body instanceof HTMLElement) {
        body.append(o.body);
      } else if (Array.isArray(o.body)) {
        o.body.forEach((n) => body.append(n));
      } else if (o.body != null) {
        body.textContent = String(o.body);
      }
      root.append(body);
    }
    if (o.footer !== undefined) {
      const foot = document.createElement("div");
      foot.className = "panel-footer";
      if (typeof o.footer === "function") {
        const result = o.footer();
        if (result instanceof HTMLElement) foot.append(result);
        else if (Array.isArray(result)) result.forEach((n) => foot.append(n));
      } else if (o.footer instanceof HTMLElement) {
        foot.append(o.footer);
      } else if (Array.isArray(o.footer)) {
        o.footer.forEach((n) => foot.append(n));
      }
      root.append(foot);
    }
    return root;
  }

  /**
   * Build a definition list from items [{dt, dd, mono}].
   * @param {Array} items
   * @param {object} [opts] {vertical}
   * @returns {HTMLDListElement|HTMLDivElement}
   */
  function createDefinitionList(items, opts) {
    const o = opts || {};
    if (o.vertical) {
      const root = document.createElement("div");
      root.className = "dl-vertical";
      (items || []).forEach((item) => {
        const row = document.createElement("div");
        row.className = "dl-row";
        const dt = document.createElement("dt");
        dt.className = "dl-dt";
        dt.textContent = String(item.dt ?? "");
        const dd = document.createElement("dd");
        dd.className = "dl-dd";
        if (item.mono) dd.classList.add("dl-dd-mono");
        // 安全规则：所有内容通过 textContent 设置；如需富文本，调用方应在传入前用 createElement 构造节点。
        if (item.node instanceof HTMLElement) {
          dd.append(item.node);
        } else {
          dd.textContent = String(item.dd ?? "");
        }
        row.append(dt, dd);
        root.append(row);
      });
      return root;
    }
    const dl = document.createElement("dl");
    dl.className = "dl";
    (items || []).forEach((item) => {
      const dt = document.createElement("dt");
      dt.className = "dl-dt";
      dt.textContent = String(item.dt ?? "");
      const dd = document.createElement("dd");
      dd.className = "dl-dd";
      if (item.mono) dd.classList.add("dl-dd-mono");
      dd.textContent = String(item.dd ?? "");
      dl.append(dt, dd);
    });
    return dl;
  }

  /**
   * Build a basic data table.
   * @param {object} opts {columns:[{key,label,width,mono,num,compact}], rows:[{cells:{key:HTMLElement|string|{text,variant}}}], onRowClick, selectedKey}
   * @returns {HTMLElement} .panel with table inside
   */
  function createTable(opts) {
    const o = opts || {};
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    const table = document.createElement("table");
    table.className = "table";
    const thead = document.createElement("thead");
    const trh = document.createElement("tr");
    (o.columns || []).forEach((col) => {
      const th = document.createElement("th");
      if (col.width) th.style.width = col.width;
      if (col.num) th.classList.add("table-cell-num");
      th.textContent = String(col.label ?? "");
      trh.append(th);
    });
    thead.append(trh);
    table.append(thead);
    const tbody = document.createElement("tbody");
    if (!o.rows || !o.rows.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = Math.max(1, (o.columns || []).length);
      td.className = "table-empty";
      td.textContent = "暂无数据";
      tr.append(td);
      tbody.append(tr);
    } else {
      o.rows.forEach((row) => {
        const tr = document.createElement("tr");
        if (o.onRowClick) {
          tr.style.cursor = "pointer";
          tr.addEventListener("click", (ev) => o.onRowClick(row, ev));
        }
        if (o.selectedKey !== undefined && row.key !== undefined
            && String(row.key) === String(o.selectedKey)) {
          tr.setAttribute("aria-selected", "true");
        }
        (o.columns || []).forEach((col) => {
          const td = document.createElement("td");
          if (col.mono) td.classList.add("table-cell-mono");
          if (col.num) td.classList.add("table-cell-num");
          if (col.compact) td.classList.add("table-cell-compact");
          const cell = row.cells?.[col.key];
          if (cell instanceof HTMLElement) {
            td.append(cell);
          } else if (typeof cell === "object" && cell !== null) {
            if (cell.variant) {
              td.append(createBadge(cell.text, cell.variant));
            } else {
              td.textContent = String(cell.text ?? "");
            }
          } else if (cell !== undefined && cell !== null) {
            td.textContent = String(cell);
          }
          tr.append(td);
        });
        tbody.append(tr);
      });
    }
    table.append(tbody);
    wrap.append(table);
    return wrap;
  }

  /**
   * Build a trace viewer for context-run sections and debug responses.
   * Auto-detects format (json/markdown/code/plain), supports copy and collapse.
   * @param {object} opts {content, format, label, collapsible}
   * @returns {HTMLElement} .trace-viewer
   */
  function createTraceViewer(opts) {
    const o = opts || {};
    const raw = Core.serializeTraceContent(o.content);
    const format = Core.detectTraceFormat(o.content, o.format);
    const root = document.createElement("div");
    root.className = "trace-viewer";

    const head = document.createElement("div");
    head.className = "trace-viewer-header";
    const meta = document.createElement("div");
    meta.className = "trace-viewer-format";
    const fmtLabel = document.createElement("span");
    fmtLabel.textContent = format;
    meta.append(fmtLabel);
    const sep = document.createElement("span");
    sep.textContent = "·";
    sep.style.opacity = "0.5";
    meta.append(sep);
    const count = document.createElement("span");
    count.textContent = `${raw.length} 字符`;
    meta.append(count);
    head.append(meta);

    const actions = document.createElement("div");
    actions.className = "trace-viewer-actions";
    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "trace-viewer-action";
    copyBtn.title = "复制";
    const copyIc = Core.icon("copy");
    copyIc.classList.add("trace-viewer-action-icon");
    copyBtn.append(copyIc);
    const copyTxt = document.createElement("span");
    copyTxt.textContent = "复制";
    copyBtn.append(copyTxt);
    copyBtn.addEventListener("click", async () => {
      const ok = await Core.copyText(raw);
      copyTxt.textContent = ok ? "已复制" : "失败";
      global.setTimeout(() => { copyTxt.textContent = "复制"; }, 1500);
    });
    actions.append(copyBtn);
    if (o.collapsible !== false && raw.length > 200) {
      const expandBtn = document.createElement("button");
      expandBtn.type = "button";
      expandBtn.className = "trace-viewer-action";
      expandBtn.title = "展开/收起";
      const expandIc = Core.icon("maximize-2");
      expandIc.classList.add("trace-viewer-action-icon");
      expandBtn.append(expandIc);
      const expandTxt = document.createElement("span");
      expandTxt.textContent = "展开";
      expandBtn.append(expandTxt);
      expandBtn.addEventListener("click", () => {
        const body = root.querySelector(".trace-viewer-body");
        const collapsed = body.dataset.collapsed === "true";
        body.dataset.collapsed = collapsed ? "false" : "true";
        expandTxt.textContent = collapsed ? "收起" : "展开";
      });
      actions.append(expandBtn);
    }
    head.append(actions);
    root.append(head);

    const body = document.createElement("div");
    body.className = "trace-viewer-body";
    if (o.collapsible !== false && raw.length > 200) {
      body.dataset.collapsed = "true";
    }
    const pre = document.createElement("pre");
    pre.className = "trace-viewer-content";
    pre.textContent = raw; // safe: backend data via textContent
    body.append(pre);
    root.append(body);
    return root;
  }

  /* ----------------------- Drawer ----------------------- */

  /**
   * Drawer instance with focus trap, ESC close, backdrop click.
   * @param {object} opts {title, subtitle, body, footer, onOpen, onClose}
   * @returns {{el:HTMLElement, open:Function, close:Function, isOpen:Function, setTitle:Function, setBody:Function}}
   */
  function createDrawer(opts) {
    const o = opts || {};
    const backdrop = document.createElement("div");
    backdrop.className = "drawer-backdrop";
    backdrop.dataset.open = "false";
    const drawer = document.createElement("aside");
    drawer.className = "drawer";
    drawer.dataset.open = "false";
    drawer.setAttribute("role", "dialog");
    drawer.setAttribute("aria-modal", "true");

    const head = document.createElement("div");
    head.className = "drawer-header";
    const titleGroup = document.createElement("div");
    titleGroup.className = "drawer-title-group";
    const title = document.createElement("div");
    title.className = "drawer-title";
    if (o.title) title.textContent = String(o.title);
    titleGroup.append(title);
    const subtitle = document.createElement("div");
    subtitle.className = "drawer-subtitle";
    if (o.subtitle) subtitle.textContent = String(o.subtitle);
    titleGroup.append(subtitle);
    head.append(titleGroup);
    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "drawer-close";
    closeBtn.setAttribute("aria-label", "关闭");
    closeBtn.append(Core.icon("x"));
    head.append(closeBtn);
    drawer.append(head);

    const body = document.createElement("div");
    body.className = "drawer-body";
    if (o.body instanceof HTMLElement) body.append(o.body);
    else if (typeof o.body === "function") {
      const result = o.body();
      if (result instanceof HTMLElement) body.append(result);
    }
    drawer.append(body);

    if (o.footer !== undefined) {
      const foot = document.createElement("div");
      foot.className = "drawer-footer";
      if (o.footer instanceof HTMLElement) foot.append(o.footer);
      else if (typeof o.footer === "function") {
        const result = o.footer();
        if (result instanceof HTMLElement) foot.append(result);
      }
      drawer.append(foot);
    }

    let lastFocused = null;
    let isOpen = false;

    function focusFirst() {
      const focusable = drawer.querySelector(
        "input, textarea, select, button:not([disabled]), [tabindex]:not([tabindex='-1'])"
      );
      if (focusable instanceof HTMLElement) focusable.focus();
      else closeBtn.focus();
    }

    function trap(ev) {
      if (!isOpen) return;
      if (ev.key === "Escape") {
        ev.preventDefault();
        close();
        return;
      }
      if (ev.key !== "Tab") return;
      const focusable = drawer.querySelectorAll(
        "input, textarea, select, button:not([disabled]), [tabindex]:not([tabindex='-1'])"
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (ev.shiftKey && document.activeElement === first) {
        ev.preventDefault();
        last.focus();
      } else if (!ev.shiftKey && document.activeElement === last) {
        ev.preventDefault();
        first.focus();
      }
    }

    function open() {
      if (isOpen) return;
      isOpen = true;
      lastFocused = document.activeElement;
      backdrop.dataset.open = "true";
      drawer.dataset.open = "true";
      document.body.append(backdrop, drawer);
      document.body.style.overflow = "hidden";
      document.addEventListener("keydown", trap, true);
      backdrop.addEventListener("click", close);
      closeBtn.addEventListener("click", close, { once: true });
      global.setTimeout(focusFirst, 60);
      if (typeof o.onOpen === "function") o.onOpen({ drawer, body });
      Core.refreshIcons();
    }

    function close() {
      if (!isOpen) return;
      isOpen = false;
      backdrop.dataset.open = "false";
      drawer.dataset.open = "false";
      document.removeEventListener("keydown", trap, true);
      document.body.style.overflow = "";
      global.setTimeout(() => {
        backdrop.remove();
        drawer.remove();
      }, 220);
      if (typeof o.onClose === "function") o.onClose();
      if (lastFocused instanceof HTMLElement) {
        lastFocused.focus();
      }
    }

    function setTitle(text, subtitleText) {
      title.textContent = String(text ?? "");
      if (subtitleText !== undefined) subtitle.textContent = String(subtitleText);
    }

    function setBody(node) {
      body.replaceChildren();
      if (node instanceof HTMLElement) body.append(node);
      else if (typeof node === "function") {
        const result = node();
        if (result instanceof HTMLElement) body.append(result);
      }
      Core.refreshIcons();
    }

    return { el: drawer, open, close, isOpen: () => isOpen, setTitle, setBody };
  }

  /* ----------------------- Toast ----------------------- */

  const toastRegion = (function ensureToastRegion() {
    let region = document.querySelector(".toast-region");
    if (!region) {
      region = document.createElement("div");
      region.className = "toast-region";
      region.setAttribute("role", "status");
      region.setAttribute("aria-live", "polite");
      document.body.append(region);
    }
    return region;
  })();

  /**
   * Show a toast notification.
   * @param {object|string} opts {title, message, variant, timeout} or message string
   * @returns {{el:HTMLElement, close:Function}}
   */
  function toast(opts) {
    const o = typeof opts === "string" ? { message: opts } : (opts || {});
    const root = document.createElement("div");
    root.className = "toast";
    if (o.variant) root.classList.add(`toast-${o.variant}`);
    else root.classList.add("toast-info");

    const iconName = o.variant === "success" ? "check"
      : o.variant === "error" ? "circle-alert"
      : o.variant === "warning" ? "circle-alert"
      : "circle-alert";
    const ic = Core.icon(iconName);
    ic.classList.add("toast-icon");
    root.append(ic);

    const content = document.createElement("div");
    content.className = "toast-content";
    if (o.title) {
      const t = document.createElement("div");
      t.className = "toast-title";
      t.textContent = String(o.title);
      content.append(t);
    }
    if (o.message) {
      const m = document.createElement("div");
      m.className = "toast-message";
      m.textContent = String(o.message);
      content.append(m);
    }
    root.append(content);

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "toast-close";
    closeBtn.setAttribute("aria-label", "关闭");
    closeBtn.append(Core.icon("x"));
    closeBtn.addEventListener("click", () => close());
    root.append(closeBtn);

    toastRegion.append(root);
    Core.refreshIcons();

    let timer = null;
    function close() {
      if (timer) { global.clearTimeout(timer); timer = null; }
      if (!root.parentNode) return;
      root.classList.add("toast-leaving");
      global.setTimeout(() => root.remove(), 220);
    }

    const timeout = Core.numberValue(o.timeout, 4000);
    if (timeout > 0) {
      timer = global.setTimeout(close, timeout);
    }
    return { el: root, close };
  }

  /**
   * Convenience helpers for common toast variants.
   */
  function toastSuccess(message, title) {
    return toast({ title: title || "成功", message, variant: "success" });
  }

  function toastError(message, title) {
    return toast({ title: title || "出错", message, variant: "error", timeout: 6000 });
  }

  function toastWarning(message, title) {
    return toast({ title: title || "提示", message, variant: "warning" });
  }

  function toastInfo(message, title) {
    return toast({ title: title, message, variant: "info" });
  }

  /* ----------------------- Records Section ----------------------- */

  /**
   * Build a list of records (evidence / audit / revision rows).
   * @param {Array<{title?, meta:[{text,variant?}], body?, mono?}>} items
   * @returns {HTMLElement} .records
   */
  function createRecords(items) {
    const root = document.createElement("div");
    root.className = "records";
    if (!items || !items.length) {
      const empty = createEmptyState({
        title: "暂无记录",
        message: "尚无相关记录。",
      });
      empty.style.padding = "var(--sp-5)";
      root.append(empty);
      return root;
    }
    items.forEach((item) => {
      const row = document.createElement("div");
      row.className = "record-item";
      const head = document.createElement("div");
      head.className = "record-item-header";
      if (item.title) {
        const t = document.createElement("div");
        t.style.fontWeight = "500";
        t.style.color = "var(--text-strong)";
        t.textContent = String(item.title);
        head.append(t);
      }
      if (Array.isArray(item.meta) && item.meta.length) {
        const meta = document.createElement("div");
        meta.className = "record-item-meta";
        item.meta.forEach((m) => {
          if (m && m.variant) {
            meta.append(createBadge(m.text, m.variant));
          } else if (m) {
            const span = document.createElement("span");
            span.textContent = String(m.text ?? m);
            meta.append(span);
          }
        });
        head.append(meta);
      }
      if (head.childNodes.length) row.append(head);
      if (item.body !== undefined && item.body !== null) {
        const body = document.createElement("div");
        body.className = "record-item-body";
        if (item.mono) {
          body.style.fontFamily = "var(--font-mono)";
          body.style.fontSize = "var(--fs-xs)";
        }
        body.textContent = String(item.body);
        row.append(body);
      }
      root.append(row);
    });
    return root;
  }

  /* ----------------------- Section Title ----------------------- */

  /**
   * Build a section title for use inside drawer/panel body.
   * @param {string} text
   * @param {string} [iconName]
   * @returns {HTMLElement}
   */
  function createSectionTitle(text, iconName) {
    const head = document.createElement("div");
    head.className = "drawer-section-title";
    if (iconName) {
      const ic = Core.icon(iconName);
      ic.style.width = "14px";
      ic.style.height = "14px";
      head.append(ic);
    }
    const span = document.createElement("span");
    span.textContent = String(text ?? "");
    head.append(span);
    return head;
  }

  /* ----------------------- Loading ----------------------- */

  /**
   * Build a small inline loading indicator.
   * @param {string} [text]
   * @returns {HTMLElement}
   */
  function createLoading(text) {
    const span = document.createElement("span");
    span.className = "loading-overlay";
    const sp = document.createElement("span");
    sp.className = "spinner";
    sp.setAttribute("aria-hidden", "true");
    span.append(sp);
    if (text) {
      const t = document.createElement("span");
      t.textContent = String(text);
      span.append(t);
    }
    return span;
  }

  /**
   * Build a skeleton placeholder block.
   * @param {number} [lines=3]
   * @returns {HTMLElement}
   */
  function createSkeleton(lines) {
    const n = Math.max(1, Core.numberValue(lines, 3));
    const root = document.createElement("div");
    for (let i = 0; i < n; i++) {
      const line = document.createElement("div");
      line.className = "skeleton skeleton-line";
      root.append(line);
    }
    return root;
  }

  global.HumanizeUi = Object.freeze({
    createButton,
    createField,
    createInput,
    createTextarea,
    createSelect,
    createBadge,
    createChip,
    createMetric,
    createTabs,
    setActiveTab,
    createPagination,
    createEmptyState,
    createAlert,
    createPanel,
    createDefinitionList,
    createTable,
    createTraceViewer,
    createDrawer,
    createRecords,
    createSectionTitle,
    createLoading,
    createSkeleton,
    toast,
    toastSuccess,
    toastError,
    toastWarning,
    toastInfo,
  });
})(window);
