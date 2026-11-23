// Paths and defaults mirror the Python case loader and the calculation stages.
const option = (value, label) => ({ value, label });
const positiveNumber = (path, label, unit, settings = {}) =>
  ({ path, label, unit, type: "number", min: 0, exclusiveMin: true, ...settings });
const numberList = (path, label, unit, settings = {}) =>
  ({ path, label, unit, type: "number-list", ...settings });

export const TASK_OPTIONS = [
  option("input", "母型输入整理"),
  option("geometry", "船体几何重建"),
  option("bonjean", "邦戎曲线"),
  option("hydrostatics", "静水力计算"),
  option("compartments", "舱室与舱容"),
  option("weights", "重量与重心"),
  option("equilibrium", "浮态与平衡"),
  option("stability", "大倾角稳性"),
  option("criteria", "风压、进水角与稳性限值"),
  option("longitudinal", "纵强度"),
  option("hold_loads", "许用货舱载荷核对"),
  option("report", "综合报告"),
  option("drawings", "设计图纸"),
];

const defaultTasks = ["geometry", "bonjean", "hydrostatics", "compartments", "report"];
const loadingTasks = new Set(["weights", "equilibrium", "stability", "criteria", "longitudinal", "hold_loads"]);
const drawingTypes = [
  option("lines_plan", "型线图"),
  option("typical_section", "典型横剖面图"),
  option("general_arrangement", "总布置图"),
  option("capacity_plan", "舱容图"),
];
const dimensionLabels = [
  ["loa_m", "总长 LOA"],
  ["lbp_m", "垂线间长 LBP"],
  ["beam_m", "型宽 B"],
  ["depth_m", "型深 D"],
  ["design_draft_m", "设计吃水 T"],
  ["scantling_draft_m", "结构吃水 Ts"],
];

export const FIELD_GROUPS = [
  {
    id: "identity", title: "算例信息",
    fields: [
      { path: "name", label: "算例名称", type: "text", placeholder: "例如 34000t 散货船" },
    ],
  },
  {
    id: "dimensions", title: "目标主尺度",
    description: "主尺度可留空，由计算程序按母型比例推算。",
    fields: dimensionLabels.map(([key, label]) => positiveNumber("design.target_dimensions." + key, label, "m")),
  },
  {
    id: "parameters", title: "目标设计参数",
    description: "排水量、载重量和方形系数以结构吃水为基准。",
    fields: [
      positiveNumber("design.target_parameters.displacement_t", "排水量 Δ", "t"),
      positiveNumber("design.target_parameters.lightweight_t", "空船重量 LW₀", "t"),
      positiveNumber("design.target_parameters.deadweight_t", "载重量 DW", "t"),
      positiveNumber("design.target_parameters.block_coefficient", "方形系数 Cb", "", { max: 1 }),
    ],
  },
  {
    id: "tasks", title: "计算与输出",
    description: "程序会自动执行选中任务所需的前置工序。",
    fields: [
      { path: "tasks", label: "计算任务", type: "choices", options: TASK_OPTIONS, defaultValue: defaultTasks, nonempty: true },
    ],
  },
  {
    id: "mother", title: "母型图纸",
    description: "填写三张 DXF 图纸的路径。相对路径以选定的算例目录为基准。",
    fields: [
      { path: "mother_ship.drawings.lines", label: "型线图", type: "text", required: true },
      { path: "mother_ship.drawings.general_arrangement", label: "总布置图", type: "text", required: true },
      { path: "mother_ship.drawings.typical_section", label: "典型横剖面图", type: "text", required: true },
    ],
  },
  {
    id: "loading", title: "装载工况",
    description: "重量与重心、浮态、稳性、纵强度或许用货舱载荷计算需要装载工况。",
    fields: [
      {
        path: "loading", label: "装载配置", type: "loading",
        placeholder: "loading.json",
        hint: "填写装载文件路径，或粘贴包含 cases 的 JSON 对象。",
      },
    ],
  },
  {
    id: "motherDimensions", title: "母型主尺度", advanced: true,
    description: "可补充或覆盖从 DXF 读取的母型主尺度。",
    fields: dimensionLabels.map(([key, label]) => positiveNumber("mother_ship.dimensions." + key, label, "m")),
  },
  {
    id: "environment", title: "计算介质", advanced: true,
    fields: [
      positiveNumber("environment.water_density_t_m3", "水密度", "t/m³", { defaultValue: 1.025 }),
      positiveNumber("environment.hydro_shell_thickness_mm", "静水力修正板厚", "mm", {
        exclusiveMin: false, defaultValue: 0, hint: "填 0 时采用型线包络。",
      }),
    ],
  },
  {
    id: "mesh", title: "网格与结构", advanced: true,
    fields: [
      { path: "mesh.samples", label: "横剖采样数", type: "integer", min: 16, defaultValue: 129 },
      { path: "mesh.subdivisions", label: "纵向细分数", type: "integer", min: 1, defaultValue: 8 },
      positiveNumber("mesh.shell_thickness_mm", "船壳板厚", "mm", { defaultValue: 12 }),
      positiveNumber("mesh.bulkhead_thickness_mm", "横舱壁板厚", "mm", { defaultValue: 8 }),
      positiveNumber("mesh.partition_thickness_mm", "内舷纵隔板厚", "mm", { defaultValue: 8 }),
      positiveNumber("mesh.steel_density_t_m3", "钢材密度", "t/m³", { defaultValue: 7.85 }),
      {
        path: "mesh.bow_closure", label: "艏部封闭方式", type: "select", defaultValue: "nominal",
        options: [option("nominal", "按总长封闭"), option("station", "按站位封闭")],
      },
      {
        path: "mesh.export_formats", label: "网格导出格式", type: "choices", defaultValue: ["obj", "stl"],
        options: [option("obj", "OBJ"), option("stl", "STL"), option("vtk", "VTK")],
      },
      { path: "mesh.export_dxf", label: "导出三维 DXF", type: "checkbox", defaultValue: false },
    ],
  },
  {
    id: "hydro", title: "邦戎曲线与静水力", advanced: true,
    fields: [
      positiveNumber("bonjean.draft_step_m", "邦戎曲线吃水步长", "m", { defaultValue: 0.5 }),
      numberList("bonjean.drafts_m", "邦戎曲线吃水列表", "m", { min: 0, exclusiveMin: true }),
      numberList("bonjean.stations_m", "邦戎曲线站位列表", "m"),
      positiveNumber("hydrostatics.draft_step_m", "静水力吃水步长", "m", { defaultValue: 0.5 }),
      numberList("hydrostatics.drafts_m", "静水力吃水列表", "m", { min: 0, exclusiveMin: true }),
      numberList("hydrostatics.trims_m", "纵倾列表 TF−TA", "m", { defaultValue: [0] }),
    ],
  },
  {
    id: "compartments", title: "分舱与舱容", advanced: true,
    fields: [
      numberList("capacity.fill_fractions", "装载率列表", "", { min: 0, max: 1, placeholder: "0, 0.25, 0.5, 0.75, 1" }),
      { path: "capacity.level_samples", label: "液位采样数", type: "integer", min: 3, defaultValue: 201 },
      positiveNumber("compartments.double_bottom_m", "双层底高", "m"),
      positiveNumber("compartments.side_tank_m", "边舱宽度", "m"),
      positiveNumber("compartments.inner_half_breadth_m", "内舷半宽", "m"),
      positiveNumber("compartments.upper_half_breadth_m", "顶部半宽", "m"),
      positiveNumber("compartments.top_slope_start_m", "顶边舱斜壁起始高度", "m"),
      positiveNumber("compartments.top_slope_end_m", "顶边舱斜壁结束高度", "m"),
      { path: "compartments.spans", label: "舱室纵向区段", type: "json", shape: "array", hint: "按机舱、5 号货舱至 1 号货舱的顺序填写。" },
      { path: "compartments.purposes", label: "舱室用途", type: "json", shape: "object", hint: "键为舱室编号，值为用途代码。" },
    ],
  },
  {
    id: "stability", title: "稳性、纵强度与载荷限值", advanced: true,
    fields: [
      numberList("stability.angles_deg", "横倾角列表", "°", { min: 0, max: 180, hint: "需包含 0° 及至少两个其他角度。" }),
      {
        path: "stability.sides", label: "横倾方向", type: "choices", defaultValue: ["port"], nonempty: true,
        options: [option("port", "左舷"), option("starboard", "右舷")],
      },
      { path: "longitudinal.cells", label: "纵向初始网格数", type: "integer", min: 20, defaultValue: 450 },
      { path: "criteria", label: "风压、进水角及稳性限值设置", type: "json", shape: "object" },
      { path: "hold_loads.envelopes", label: "货舱许用载荷曲线", type: "json", shape: "array" },
    ],
  },
  {
    id: "drawings", title: "设计图纸设置", advanced: true,
    fields: [
      {
        path: "drawings.template", label: "出图版式", type: "select", defaultValue: "mother_35000",
        options: [option("mother_35000", "35000 t 母型版式"), option("simple", "简化版式")],
      },
      {
        path: "drawings.types", label: "图纸类型", type: "choices", options: drawingTypes, nonempty: true,
        defaultValue: drawingTypes.map(({ value }) => value),
      },
      { path: "drawings.structure_details", label: "绘制典型横剖面参考构件", type: "checkbox", defaultValue: true },
      { path: "drawings.revision", label: "修订子目录", type: "text", placeholder: "例如 R01" },
      numberList("drawings.stations_m", "图纸站位列表", "m"),
      numberList("drawings.waterlines_m", "水线列表", "m"),
      numberList("drawings.buttocks_m", "纵剖半宽列表", "m", { min: 0 }),
      { path: "drawings.section_x_m", label: "典型横剖面位置", unit: "m", type: "number" },
      positiveNumber("drawings.plan_z_m", "水平剖面高度", "m"),
    ],
  },
];

const fields = FIELD_GROUPS.flatMap(({ fields }) => fields);
const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const decimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;

export function createCase() {
  return {
    schema_version: "1.0",
    name: "新算例",
    mother_ship: {
      adapter: "mother_ship_offsets_v1",
      drawings: {
        lines: "../example_ship/drawings/lines.dxf",
        general_arrangement: "../example_ship/drawings/arrangement.dxf",
        typical_section: "../example_ship/drawings/section.dxf",
      },
    },
    design: { method: "affine", target_dimensions: {} },
    tasks: [...defaultTasks, "drawings"],
  };
}

export function parseCase(text) {
  const value = parseJson(text);
  if (!record(value)) {
    throw new Error("算例文件必须包含一个 JSON 对象。");
  }
  return value;
}

export function serializeCase(config) {
  if (!record(config)) {
    throw new Error("算例配置必须为 JSON 对象。");
  }
  return JSON.stringify(config, (_, value) => {
    if (typeof value === "number" && !Number.isFinite(value)) {
      throw new Error("算例中的数字必须为有限值。");
    }
    return value;
  }, 2) + "\n";
}

export function getFieldValue(config, path) {
  return path.split(".").reduce((value, key) =>
    record(value) && Object.hasOwn(value, key) ? value[key] : undefined, config);
}

export function setFieldValue(config, field, input) {
  const path = field.path.split(".");
  const key = path.pop();
  const value = input === undefined || (typeof input === "string" && !input.trim())
    ? undefined
    : parseInput(field, input);
  if (value !== undefined) {
    const error = validateField(field, value);
    if (error) {
      throw new Error(error);
    }
  }
  let target = config;
  for (const part of path) {
    if (!record(target[part])) {
      if (value === undefined) return;
      target[part] = {};
    }
    target = target[part];
  }
  if (value === undefined) {
    delete target[key];
  } else {
    target[key] = value;
  }
}

function parseJson(text) {
  try {
    return JSON.parse(text.replace(/^\uFEFF/, ""), (_, value) => {
      if (typeof value === "number" && !Number.isFinite(value)) {
        throw new Error("数字必须为有限值。");
      }
      return value;
    });
  } catch (error) {
    throw new Error("JSON 格式无效。" + error.message);
  }
}

function parseInput(field, input) {
  if (typeof input !== "string") return input;
  const value = input.trim();
  switch (field.type) {
    case "number":
    case "integer":
      return decimal.test(value) ? Number(value) : NaN;
    case "number-list":
      if (value.startsWith("[")) return parseJson(value);
      return value.split(/[\s,，、;；]+/).filter(Boolean)
        .map((entry) => decimal.test(entry) ? Number(entry) : NaN);
    case "json":
      return parseJson(value);
    case "loading":
      return value.startsWith("{") || value.startsWith("[") || value.startsWith('"') ? parseJson(value) : value;
    default:
      return value;
  }
}

function validateNumber(field, value) {
  if (!Number.isFinite(value)) return "请输入有限数值。";
  if (field.type === "integer" && !Number.isSafeInteger(value)) return "请输入整数。";
  if (field.exclusiveMin && value <= field.min) return `数值必须大于 ${field.min}。`;
  if (value < field.min) return `数值不能小于 ${field.min}。`;
  if (value > field.max) return `数值不能大于 ${field.max}。`;
  return "";
}

function validateField(field, value) {
  if (value === undefined) return field.required ? "此项必填。" : "";
  switch (field.type) {
    case "text":
      return typeof value === "string" && (!field.required || value.trim()) ? "" : "请输入文本。";
    case "number":
    case "integer":
      return validateNumber(field, value);
    case "select":
      return field.options.some((item) => item.value === value) ? "" : "请选择可用选项。";
    case "checkbox":
      return typeof value === "boolean" ? "" : "此项应为布尔值。";
    case "choices":
      if (field.nonempty && Array.isArray(value) && !value.length) return "请至少选择一项。";
      return Array.isArray(value) && value.every((entry) => field.options.some((item) => item.value === entry))
        ? "" : "请选择可用选项。";
    case "number-list":
      if (!Array.isArray(value) || !value.length) return "请输入至少一个数值。";
      return value.map((entry) => validateNumber(field, entry)).find(Boolean) || "";
    case "json":
      if (field.shape === "array") return Array.isArray(value) ? "" : "请输入 JSON 数组。";
      return record(value) ? "" : "请输入 JSON 对象。";
    case "loading":
      return value === null || record(value) || (typeof value === "string" && value.trim())
        ? "" : "请输入装载文件路径或 JSON 对象。";
  }
  return "";
}

export function validateCase(config) {
  const issues = [];
  const add = (path, message) => issues.push({ path, message });
  if (!record(config)) {
    add("", "算例配置必须为 JSON 对象。");
    return issues;
  }
  if (config.schema_version !== "1.0") add("schema_version", '格式版本须为 "1.0"。');
  if (getFieldValue(config, "mother_ship.adapter") !== "mother_ship_offsets_v1") {
    add("mother_ship.adapter", "母型图纸适配器须为 mother_ship_offsets_v1。");
  }
  const method = getFieldValue(config, "design.method");
  if (!record(config.design) || (method !== undefined && method !== "affine")) {
    add("design.method", "算例须包含 design 对象，设计方法须为 affine。");
  }
  for (const path of [
    "mother_ship.drawings", "mother_ship.dimensions", "design.target_dimensions", "design.target_parameters",
    "environment", "mesh", "bonjean", "hydrostatics", "capacity", "compartments", "stability", "longitudinal",
    "hold_loads", "drawings",
  ]) {
    const value = getFieldValue(config, path);
    if (value !== undefined && !record(value)) add(path, "此项应为 JSON 对象。");
  }
  for (const field of fields) {
    const message = validateField(field, getFieldValue(config, field.path));
    if (message) add(field.path, "请检查" + field.label + "。" + message);
  }

  const seenDrawings = new Set();
  for (const role of ["lines", "general_arrangement", "typical_section"]) {
    const path = "mother_ship.drawings." + role;
    const value = getFieldValue(config, path);
    if (typeof value !== "string" || !value.trim()) continue;
    if (!value.toLowerCase().endsWith(".dxf")) add(path, "请输入 .dxf 图纸路径。");
    const name = value.split(/[\\/]/).at(-1);
    if (seenDrawings.has(name)) add(path, "三张图纸的文件名必须各不相同。");
    seenDrawings.add(name);
  }

  const dimensions = getFieldValue(config, "design.target_dimensions") || {};
  const { loa_m: loa, lbp_m: lbp, depth_m: depth } = dimensions;
  if (Number.isFinite(loa) && Number.isFinite(lbp) && loa <= lbp) {
    add("design.target_dimensions.loa_m", "总长必须大于垂线间长。");
  }
  for (const [key, label] of dimensionLabels.slice(-2)) {
    if (Number.isFinite(dimensions[key]) && Number.isFinite(depth) && dimensions[key] > depth) {
      add("design.target_dimensions." + key, label + "不能大于型深。");
    }
  }
  const revision = getFieldValue(config, "drawings.revision");
  if (typeof revision === "string" && (/^(?:[a-z]:|[\\/])/i.test(revision) || revision.split(/[\\/]/).includes(".."))) {
    add("drawings.revision", "请填写出图目录下的相对路径。");
  }
  const angles = getFieldValue(config, "stability.angles_deg");
  if (Array.isArray(angles) && (!angles.includes(0) || new Set(angles).size < 3)) {
    add("stability.angles_deg", "横倾角列表需包含 0° 及至少两个其他角度。");
  }

  const tasks = config.tasks ?? defaultTasks;
  if (Array.isArray(tasks)) {
    if (tasks.some((task) => loadingTasks.has(task)) && !config.loading) {
      add("loading", "所选任务需要装载工况。");
    }
    if (tasks.includes("criteria") && (!record(config.criteria) || !Object.keys(config.criteria).length)) {
      add("criteria", "稳性限值任务需要相应设置。");
    }
    const envelopes = getFieldValue(config, "hold_loads.envelopes");
    if (tasks.includes("hold_loads") && (!Array.isArray(envelopes) || !envelopes.length)) {
      add("hold_loads.envelopes", "许用货舱载荷核对需要至少一条载荷曲线。");
    }
  }
  if (record(config.loading)) {
    const cases = config.loading.cases;
    const ids = Array.isArray(cases) ? cases.map((item) => item?.case_id) : [];
    if (!ids.length || ids.some((id) => typeof id !== "string" || !/^[A-Za-z0-9_-]+$/.test(id))) {
      add("loading", "装载配置须包含 cases，各工况编号只能用英文字母、数字、下划线或连字符。");
    } else if (new Set(ids).size !== ids.length) {
      add("loading", "各装载工况的编号不能重复。");
    }
  }
  return issues;
}
