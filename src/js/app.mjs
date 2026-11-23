import {
  FIELD_GROUPS, TASK_OPTIONS, createCase, getFieldValue, parseCase, serializeCase, setFieldValue, validateCase,
} from "./case-model.mjs";

const panels = [
  { id: "design", title: "主尺度与参数", groups: ["dimensions", "parameters"] },
  { id: "source", title: "算例与母型", groups: ["identity", "mother", "loading"] },
  { id: "tasks", title: "计算与图纸", groups: ["tasks", "drawings"] },
  { id: "advanced", title: "高级设置", description: "按需覆盖模型和计算默认值。", groups: ["motherDimensions", "environment", "mesh", "hydro", "compartments", "stability"] },
];

const $ = (id) => document.getElementById(id);
const templateSelect = $("template-select");
const fileInput = $("case-file");
const loadingFile = $("loading-file");
const editor = $("editor-content");
const sectionNav = $("section-nav");
const notice = $("notice");
const runButton = $("run-button");
const logPanel = $("log-panel");
const runLog = $("run-log");
const fields = new Map();
const fieldErrors = new Map();
const fieldDefinitions = new Map(FIELD_GROUPS.flatMap(({ fields }) => fields.map((field) => [field.path, field])));
const draftStorageKey = "fsgy-surf.draft.v1";
const runIdPattern = /^[a-f0-9]{32}$/;

let templates = [];
let draft;
let templateChoice;
let importedName;
let baseId = "example";
let activePanel = "design";
let dirty = false;
let draftSaveTimer;
let runBusy = false;
let activeRunId;
let activeRunName;
let pollTimer;
const completedLabels = { succeeded: "计算完成", failed: "计算失败", interrupted: "计算中断" };

function readSavedDraft() {
  try {
    return JSON.parse(window.sessionStorage.getItem(draftStorageKey));
  } catch {
    return null;
  }
}

function saveDraft() {
  clearTimeout(draftSaveTimer);
  if (!draft) return;
  try {
    window.sessionStorage.setItem(draftStorageKey, JSON.stringify({
      config: draft, templateChoice, importedName, baseId, activePanel, dirty,
    }));
  } catch {
    // The page remains usable if storage is unavailable or the draft is too large.
  }
}

function queueDraftSave() {
  clearTimeout(draftSaveTimer);
  draftSaveTimer = setTimeout(saveDraft, 250);
}

window.addEventListener("pagehide", saveDraft);

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function addOptions(select, options) {
  for (const option of options) {
    const value = typeof option === "string" ? option : option.value;
    const label = typeof option === "string" ? option : option.label;
    select.add(new Option(label, value));
  }
}

function activatePanel(id) {
  activePanel = id;
  for (const { id: panelId } of panels) {
    $(`panel-${panelId}`).hidden = panelId !== id;
    $(`nav-${panelId}`).setAttribute("aria-current", String(panelId === id));
  }
  queueDraftSave();
}

function showNotice(message, kind = "info", issues = []) {
  notice.hidden = !message;
  notice.dataset.kind = kind;
  notice.replaceChildren();
  if (!message) return;
  notice.append(element("p", "", message));
  if (!issues.length) return;
  const list = element("ul");
  for (const { path, message: detail } of issues) {
    const item = element("li");
    const entry = findField(path);
    if (entry) {
      const link = element("a", "", detail);
      link.href = `#${entry.control.id}`;
      link.addEventListener("click", (event) => {
        event.preventDefault();
        focusField(entry);
      });
      item.append(link);
    } else {
      item.textContent = detail;
    }
    list.append(item);
  }
  notice.append(list);
}

function focusField({ panelId, control }) {
  activatePanel(panelId);
  const group = control.closest("details");
  if (group) group.open = true;
  (control.querySelector("input") ?? control).focus();
}

function findField(path) {
  return fields.get(path) ?? [...fields].find(([key]) => (
    path.startsWith(`${key}.`) || key.startsWith(`${path}.`)
  ))?.[1];
}

function showFieldError(entry, message) {
  entry.error.textContent = message;
  entry.error.hidden = !message;
  entry.control.setAttribute("aria-invalid", String(Boolean(message)));
}

function displayedValue(field) {
  return getFieldValue(draft, field.path) ?? field.defaultValue;
}

function updateSummary() {
  $("summary-name").textContent = draft?.name || "未命名";
  const tasks = draft?.tasks ?? fieldDefinitions.get("tasks")?.defaultValue;
  const count = fieldErrors.has("tasks")
    ? fields.get("tasks").control.querySelectorAll("input:checked").length
    : Array.isArray(tasks) ? tasks.length : undefined;
  $("summary-tasks").textContent = count == null ? "—" : `${count} 项`;
}

function updateField(field, value) {
  const entry = fields.get(field.path);
  dirty = true;
  try {
    setFieldValue(draft, field, value);
    fieldErrors.delete(field.path);
    showFieldError(entry, "");
  } catch (error) {
    fieldErrors.set(field.path, error.message);
    showFieldError(entry, error.message);
  }
  updateSummary();
  queueDraftSave();
}

function renderChoices(field, value, id) {
  const control = element("fieldset", "choices");
  control.id = id;
  const legend = element("legend", "field-label", field.label);
  control.append(legend);
  const choices = new Map();
  for (const option of field.options ?? []) {
    const key = typeof option === "string" ? option : option.value;
    const label = element("label", "choice");
    const checkbox = element("input");
    checkbox.type = "checkbox";
    checkbox.value = key;
    checkbox.checked = Array.isArray(value) && value.includes(key);
    choices.set(key, checkbox);
    label.append(checkbox, element("span", "", typeof option === "string" ? option : option.label));
    control.append(label);
  }
  control.addEventListener("change", () => {
    const previous = displayedValue(field);
    const selected = Array.isArray(previous) ? previous.filter((item) => choices.get(item)?.checked) : [];
    for (const [key, checkbox] of choices) {
      if (checkbox.checked && !selected.includes(key)) selected.push(key);
    }
    updateField(field, selected);
  });
  return control;
}

function renderControl(field, value, id) {
  let control;
  if (field.type === "select") {
    control = element("select", "control");
    if (value == null) addOptions(control, [{ value: "", label: "采用默认值" }]);
    addOptions(control, field.options ?? []);
    if (value != null && ![...control.options].some((option) => option.value === String(value))) {
      addOptions(control, [{ value: String(value), label: String(value) }]);
    }
    control.value = value ?? "";
  } else if (field.type === "loading" || field.type === "json") {
    control = element("textarea", "control");
    control.rows = field.type === "loading" ? 3 : 4;
    control.spellcheck = false;
    control.value = typeof value === "string" && field.type === "loading"
      ? value : value == null ? "" : JSON.stringify(value, null, 2);
  } else {
    control = element("input", "control");
    if (field.type === "number" || field.type === "integer") {
      control.type = "number";
      control.step = field.type === "integer" ? "1" : "any";
      if (field.min != null) control.min = field.min;
      if (field.max != null) control.max = field.max;
    }
    control.value = Array.isArray(value) ? value.join(", ") : value ?? "";
  }
  control.id = id;
  control.name = field.path;
  if (field.placeholder) control.placeholder = field.placeholder;
  control.required = Boolean(field.required);
  const eventName = field.type === "select" ? "change" : "input";
  control.addEventListener(eventName, () => {
    if (control.validity.badInput) {
      dirty = true;
      const message = `${field.label}须填写有效数字。`;
      fieldErrors.set(field.path, message);
      showFieldError(fields.get(field.path), message);
      return;
    }
    updateField(field, control.value);
  });
  return control;
}

function renderField(field, panelId) {
  const id = `field-${field.path.replaceAll(".", "-")}`;
  const wrapper = element("div", "field");
  const value = displayedValue(field);
  let control;
  if (field.type === "choices") {
    wrapper.classList.add("wide");
    control = renderChoices(field, value, id);
    wrapper.append(control);
  } else {
    const label = element("label", field.type === "checkbox" ? "choice" : "field-label", field.label);
    label.htmlFor = id;
    if (field.type === "checkbox") {
      control = element("input");
      control.type = "checkbox";
      control.id = id;
      control.checked = Boolean(value);
      control.addEventListener("change", () => updateField(field, control.checked));
      label.prepend(control);
      wrapper.append(label);
    } else {
      if (["text", "json", "loading"].includes(field.type)) wrapper.classList.add("wide");
      control = renderControl(field, value, id);
      const inputWrap = element("div", "input-wrap");
      inputWrap.append(control);
      if (field.unit) {
        label.setAttribute("aria-label", `${field.label}，单位 ${field.unit}`);
        inputWrap.classList.add("has-unit");
        inputWrap.append(element("span", "unit", field.unit));
      }
      wrapper.append(label, inputWrap);
    }
  }

  const describedBy = [];
  if (field.hint) {
    const hint = element("p", "field-hint", field.hint);
    hint.id = `${id}-hint`;
    describedBy.push(hint.id);
    wrapper.append(hint);
  }
  const error = element("p", "field-error");
  error.id = `${id}-error`;
  error.hidden = true;
  describedBy.push(error.id);
  control.setAttribute("aria-describedby", describedBy.join(" "));
  wrapper.append(error);
  fields.set(field.path, { error, control, panelId });

  if (field.type === "loading") {
    const attach = element("button", "button secondary field-actions", "载入装载 JSON");
    attach.type = "button";
    attach.addEventListener("click", () => loadingFile.click());
    wrapper.append(attach);
  }
  return wrapper;
}

function renderPathBase() {
  const wrapper = element("div", "field wide");
  const label = element("label", "field-label", "相对路径基准");
  const select = element("select", "control");
  select.id = "path-base";
  label.htmlFor = select.id;
  const hint = element("p", "field-hint");
  hint.id = "path-base-hint";
  select.setAttribute("aria-describedby", hint.id);
  addOptions(select, templates.map(({ id }) => ({ value: id, label: `cases/${id}/` })));
  select.value = baseId;
  const updateHint = () => {
    hint.textContent = `DXF 和装载文件的相对路径以 cases/${baseId}/ 为起点，也可输入本机绝对路径。`;
  };
  select.addEventListener("change", () => {
    baseId = select.value;
    dirty = true;
    updateHint();
    queueDraftSave();
  });
  updateHint();
  wrapper.append(label, select, hint);
  return wrapper;
}

function renderGroup(group, panelId) {
  const collapsible = panelId === "advanced" || group.id === "drawings";
  const wrapper = element(collapsible ? "details" : "section", "field-group");
  wrapper.dataset.group = group.id;
  wrapper.append(element(collapsible ? "summary" : "h3", "", group.title));
  if (group.description) wrapper.append(element("p", "group-description", group.description));
  const grid = element("div", "field-grid");
  for (const field of group.fields) grid.append(renderField(field, panelId));
  if (group.id === "identity") grid.append(renderPathBase());
  wrapper.append(grid);
  return wrapper;
}

function renderForm() {
  fields.clear();
  fieldErrors.clear();
  editor.replaceChildren();
  sectionNav.replaceChildren();
  const groups = new Map(FIELD_GROUPS.map((group) => [group.id, group]));
  for (const panel of panels) {
    const button = element("button", "nav-button", panel.title);
    button.type = "button";
    button.id = `nav-${panel.id}`;
    button.setAttribute("aria-controls", `panel-${panel.id}`);
    button.addEventListener("click", () => activatePanel(panel.id));
    sectionNav.append(button);
    const content = element("section");
    content.id = `panel-${panel.id}`;
    const heading = element("h2", "panel-title", panel.title);
    heading.id = `heading-${panel.id}`;
    content.setAttribute("aria-labelledby", heading.id);
    content.append(heading);
    if (panel.description) content.append(element("p", "panel-description", panel.description));
    for (const id of panel.groups) {
      if (groups.has(id)) content.append(renderGroup(groups.get(id), panel.id));
    }
    editor.append(content);
  }
  sectionNav.hidden = false;
  activatePanel(activePanel);
  updateSummary();
}

async function api(url, options) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error ?? `请求失败（HTTP ${response.status}）。`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function selectTemplate(choice) {
  templateChoice = choice;
  templateSelect.value = choice;
}

function applyDraft(config, pathBase, choice, saved = {}) {
  draft = config;
  baseId = pathBase;
  dirty = Boolean(saved.dirty);
  importedName = choice === "@imported" ? saved.importedName : undefined;
  templateSelect.querySelector('option[value="@imported"]')?.remove();
  if (choice === "@imported") {
    templateSelect.add(new Option(importedName ? `已导入 ${importedName}` : "已导入算例", choice));
  }
  selectTemplate(choice);
  renderForm();
  showNotice("");
}

async function loadTemplate(id) {
  if (id === "@new") {
    applyDraft(createCase(), "example", id);
  } else {
    const { config } = await api(`/api/templates/${encodeURIComponent(id)}`);
    applyDraft(config, id, id);
  }
}

function discardEdits() {
  return !dirty || window.confirm("放弃当前未导出的表单修改？");
}

templateSelect.addEventListener("change", async () => {
  const id = templateSelect.value;
  if (id === templateChoice) return;
  if (!discardEdits()) {
    templateSelect.value = templateChoice;
    return;
  }
  templateSelect.disabled = true;
  $("import-case").disabled = true;
  try {
    await loadTemplate(id);
  } catch (error) {
    templateSelect.value = templateChoice;
    showNotice(`读取模板失败。 ${error.message}`, "error");
  } finally {
    templateSelect.disabled = false;
    $("import-case").disabled = false;
  }
});

$("import-case").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", async () => {
  const [file] = fileInput.files;
  fileInput.value = "";
  if (!file) return;
  try {
    const config = parseCase(await file.text());
    if (!discardEdits()) return;
    applyDraft(config, baseId, "@imported", { importedName: file.name });
    showNotice(`已导入 ${file.name}。若文件引用相对路径，请在「算例与母型」核对路径基准。`);
  } catch (error) {
    showNotice(`导入失败。 ${error.message}`, "error");
  }
});

loadingFile.addEventListener("change", async () => {
  const [file] = loadingFile.files;
  loadingFile.value = "";
  if (!file) return;
  const target = draft;
  try {
    const config = parseCase(await file.text());
    if (draft !== target) return;
    const field = fieldDefinitions.get("loading");
    const text = JSON.stringify(config, null, 2);
    fields.get(field.path).control.value = text;
    updateField(field, text);
    if (fieldErrors.has(field.path)) {
      reportIssues([{ path: field.path, message: fieldErrors.get(field.path) }], "请检查装载配置。");
    } else {
      showNotice(`已将 ${file.name} 的装载工况写入表单。`);
    }
  } catch (error) {
    showNotice(`读取装载 JSON 失败。 ${error.message}`, "error");
  }
});

function reportIssues(issues, message) {
  for (const { path, message: detail } of issues) {
    const entry = findField(path);
    if (entry) showFieldError(entry, detail);
  }
  showNotice(message, "error", issues);
  const first = findField(issues[0].path);
  if (first) focusField(first);
}

function editedIssues() {
  return [...fieldErrors].map(([path, message]) => ({ path, message }));
}

$("export-case").addEventListener("click", () => {
  if (fieldErrors.size) {
    reportIssues(editedIssues(), "请先修正格式错误，再导出 JSON。");
    return;
  }
  let json;
  try {
    json = serializeCase(draft);
  } catch (error) {
    showNotice(`导出失败。 ${error.message}`, "error");
    return;
  }
  const blob = new Blob([json], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = element("a");
  link.href = url;
  link.download = "case.json";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  dirty = false;
  queueDraftSave();
  showNotice(`已导出 case.json。若保留相对路径，可将它放到 cases/${baseId}/ 或等效目录使用。`);
});

function setBusy(value) {
  runBusy = value;
  runButton.disabled = value || !draft;
  runButton.textContent = value ? "计算进行中…" : "开始计算";
}

function showRunStatus(status, label, message = "") {
  $("run-status").dataset.status = status;
  if ($("status-text").textContent !== label) $("status-text").textContent = label;
  $("run-message").textContent = message;
  $("run-message").hidden = !message;
}

function showRunOutputs(artifacts) {
  const list = $("output-links");
  list.replaceChildren();
  for (const { label, url } of artifacts) {
    if (!url.startsWith(`/runs/${encodeURIComponent(activeRunId)}/`)) continue;
    const item = element("li");
    const link = element("a", "", label);
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener";
    item.append(link);
    list.append(item);
  }
  $("output-panel").hidden = list.childElementCount === 0;
}

function updateRunUrl(id) {
  const url = new URL(window.location.href);
  if (id) {
    url.searchParams.set("run", id);
  } else {
    url.searchParams.delete("run");
  }
  window.history.replaceState(window.history.state, "", url);
}

function trackRun(id) {
  activeRunId = id;
  updateRunUrl(id);
  setBusy(true);
  showRunStatus("running", "正在读取计算状态…");
  refreshRun(id);
}

function runningLabel(log) {
  const stages = [...log.matchAll(/^\[(\d{2})\] (RUN|CACHE) /gm)];
  const [, number, action] = stages.at(-1) ?? [];
  const task = TASK_OPTIONS[Number(number) - 1];
  return task ? `${action === "CACHE" ? "读取缓存" : "正在计算"} · ${number} ${task.label}` : "正在计算";
}

async function refreshRun(id) {
  clearTimeout(pollTimer);
  $("retry-status").hidden = true;
  try {
    const result = await api(`/api/runs/${encodeURIComponent(id)}`);
    if (id !== activeRunId) return;
    activeRunName = result.name ?? activeRunName;
    const nearBottom = runLog.scrollHeight - runLog.scrollTop - runLog.clientHeight < 20;
    if (runLog.textContent !== result.log) runLog.textContent = result.log;
    logPanel.hidden = !result.log;
    if (nearBottom) runLog.scrollTop = runLog.scrollHeight;

    if (result.status === "running") {
      const label = runningLabel(result.log);
      showRunStatus("running", activeRunName ? `${activeRunName} · ${label}` : label);
      pollTimer = setTimeout(() => refreshRun(id), 1000);
    } else {
      const label = completedLabels[result.status] ?? "计算失败";
      showRunStatus(result.status, activeRunName ? `${activeRunName} · ${label}` : label, result.error);
      showRunOutputs(result.artifacts ?? []);
      if (result.status !== "succeeded") logPanel.open = true;
      setBusy(false);
    }
  } catch (error) {
    if (id !== activeRunId) return;
    if (error.status === 404) {
      activeRunId = undefined;
      updateRunUrl();
      showRunStatus("failed", "找不到该次运行", "可重新提交计算。");
      setBusy(false);
      return;
    }
    showRunStatus("running", "计算状态暂不可用", error.message);
    $("retry-status").hidden = false;
    pollTimer = setTimeout(() => refreshRun(id), 4000);
  }
}

$("retry-status").addEventListener("click", () => refreshRun(activeRunId));
$("case-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (runBusy || !draft) return;
  for (const entry of fields.values()) showFieldError(entry, "");
  const issues = fieldErrors.size ? editedIssues() : validateCase(draft);
  if (issues.length) {
    reportIssues(issues, "请检查算例输入。");
    return;
  }

  showNotice("");
  showRunOutputs([]);
  logPanel.hidden = true;
  logPanel.open = false;
  runLog.textContent = "";
  $("retry-status").hidden = true;
  activeRunName = draft.name || "未命名";
  saveDraft();
  setBusy(true);
  showRunStatus("running", `${activeRunName} · 正在提交`);
  try {
    const { id, reused } = await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config: draft, baseId }),
    });
    trackRun(id);
    if (reused) showNotice("已复用相同输入的计算结果。");
  } catch (error) {
    showRunStatus("failed", "提交失败", error.message);
    setBusy(false);
  }
});

async function restoreRun() {
  const requested = new URL(window.location.href).searchParams.get("run");
  if (runIdPattern.test(requested ?? "")) {
    trackRun(requested);
    return;
  }
  try {
    const { id } = await api("/api/runs/latest");
    if (runIdPattern.test(id ?? "")) {
      trackRun(id);
      return;
    }
  } catch {
    // Earlier workbench servers can still open a specific run URL.
  }
  setBusy(false);
}

async function initialize() {
  try {
    ({ templates } = await api("/api/templates"));
    const defaultTemplate = templates.find(({ id }) => id === "34000t") ?? templates[0];
    templateSelect.replaceChildren();
    addOptions(templateSelect, [
      ...templates.map(({ id, name }) => ({ value: id, label: name || id })),
      { value: "@new", label: "新建算例" },
    ]);
    const saved = readSavedDraft();
    const fallbackId = defaultTemplate?.id ?? "@new";
    if (saved?.config && typeof saved.config === "object" && !Array.isArray(saved.config)) {
      const knownTemplate = (id) => templates.some((template) => template.id === id);
      const choice = ["@new", "@imported"].includes(saved.templateChoice) || knownTemplate(saved.templateChoice)
        ? saved.templateChoice : fallbackId;
      const pathBase = knownTemplate(saved.baseId) ? saved.baseId : defaultTemplate?.id ?? "example";
      if (panels.some(({ id }) => id === saved.activePanel)) activePanel = saved.activePanel;
      applyDraft(saved.config, pathBase, choice, saved);
    } else {
      await loadTemplate(fallbackId);
    }
    templateSelect.disabled = false;
    $("import-case").disabled = false;
    $("export-case").disabled = false;
    await restoreRun();
  } catch (error) {
    editor.replaceChildren(element("p", "loading-message", "算例加载失败。"));
    showNotice(`无法读取本地算例。 ${error.message}`, "error");
  }
}

initialize();
