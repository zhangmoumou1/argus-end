import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import dotenv from 'dotenv';
import { chromium, firefox, webkit } from 'playwright';
import { PlaywrightAgent } from '@midscene/web/playwright';

dotenv.config({ path: path.resolve(process.cwd(), '.env') });

const BOOTSTRAP_PATH = path.resolve(process.cwd(), '.runner-bootstrap.json');
const EXPLICIT_SERVER = process.env.UI_RUNNER_SERVER || '';
const EXPLICIT_TOKEN = process.env.UI_RUNNER_TOKEN || '';
const LOGIN_USERNAME = process.env.UI_RUNNER_USERNAME || '';
const LOGIN_PASSWORD = process.env.UI_RUNNER_PASSWORD || '';
const EXPLICIT_PROJECT_ID = Number(process.env.UI_RUNNER_PROJECT_ID || 0);
const EXPLICIT_PLAN_ID = Number(process.env.UI_RUNNER_PLAN_ID || 0);
const EXPLICIT_RUN_ID = Number(process.env.UI_RUNNER_RUN_ID || 0);
const EXPLICIT_ANY_PROJECT = process.env.UI_RUNNER_ANY_PROJECT;
const POLL_INTERVAL_MS = Number(process.env.UI_RUNNER_POLL_INTERVAL_MS || 5000);
const EMPTY_POLL_MAX_INTERVAL_MS = Number(process.env.UI_RUNNER_EMPTY_POLL_MAX_INTERVAL_MS || 30000);
const ARTIFACT_UPLOAD_ATTEMPTS = Math.max(1, Number(process.env.UI_RUNNER_ARTIFACT_UPLOAD_ATTEMPTS || 1));
const ARTIFACT_UPLOAD_CONCURRENCY = Math.max(1, Number(process.env.UI_RUNNER_ARTIFACT_UPLOAD_CONCURRENCY || 3));
const DEFAULT_BROWSER = process.env.UI_RUNNER_BROWSER || 'chromium';
const DEFAULT_HEADLESS = String(process.env.UI_RUNNER_HEADLESS || 'true').toLowerCase() !== 'false';

function normalizeServerBase(server) {
  const value = String(server || '').trim().replace(/\/+$/, '');
  if (!value) {
    return 'http://127.0.0.1:7777/argus';
  }
  return /\/argus$/i.test(value) ? value : `${value}/argus`;
}

const browserMap = { chromium, firefox, webkit };
let bootstrapConfig = {};
let runtimeServer = normalizeServerBase(EXPLICIT_SERVER || 'http://127.0.0.1:7777');
let runtimeToken = EXPLICIT_TOKEN;
let runtimeProjectId = EXPLICIT_PROJECT_ID;
let runtimePlanId = EXPLICIT_PLAN_ID;
let preferredRunId = EXPLICIT_RUN_ID;
let preferredRunIds = EXPLICIT_RUN_ID ? [EXPLICIT_RUN_ID] : [];
let runtimeAnyProject = parseBool(EXPLICIT_ANY_PROJECT, !EXPLICIT_PROJECT_ID && !EXPLICIT_RUN_ID);
let midsceneModelReady = false;
let emptyPollCount = 0;

function getIdlePollIntervalMs() {
  const base = Math.max(1000, Number(POLL_INTERVAL_MS || 5000));
  const maxInterval = Math.max(base, Number(EMPTY_POLL_MAX_INTERVAL_MS || 30000));
  const factor = Math.min(emptyPollCount, 3);
  return Math.min(base * (2 ** factor), maxInterval);
}

function isPlainObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value);
}

function parseJsonLike(value) {
  if (typeof value !== 'string') {
    return value;
  }
  const text = value.trim();
  if (!text) {
    return value;
  }
  try {
    return JSON.parse(text);
  } catch {
    return value;
  }
}

function normalizePlainObject(value) {
  const parsed = parseJsonLike(value);
  return isPlainObject(parsed) ? parsed : {};
}

function normalizeArray(value) {
  const parsed = parseJsonLike(value);
  return Array.isArray(parsed) ? parsed : [];
}

function hasUnresolvedTemplate(value) {
  return typeof value === 'string' && (/\$\{[^}]+\}/.test(value) || /\{\{[^}]+\}\}/.test(value));
}

function pickTemplateSource(...values) {
  let fallback = '';
  for (const value of values) {
    const text = String(value ?? '').trim();
    if (!text) {
      continue;
    }
    if (!fallback) {
      fallback = value;
    }
    if (!hasUnresolvedTemplate(text)) {
      return value;
    }
  }
  return fallback;
}

function parseBool(value, defaultValue = false) {
  if (value === true || value === false) {
    return value;
  }
  if (value === null || value === undefined || value === '') {
    return defaultValue;
  }
  return ['1', 'true', 'yes', 'on'].includes(String(value).trim().toLowerCase());
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

class RunCancelledError extends Error {
  constructor(message = 'UI测试执行已被手动停止') {
    super(message);
    this.name = 'RunCancelledError';
    this.code = 'RUN_CANCELLED';
  }
}

function isRunCancelledError(error) {
  return error?.code === 'RUN_CANCELLED' || error?.name === 'RunCancelledError';
}

function maskToken(token) {
  const value = String(token || '').trim();
  if (!value) {
    return '';
  }
  if (value.length <= 12) {
    return value;
  }
  return `${value.slice(0, 6)}***${value.slice(-4)}`;
}

function getAuthMode() {
  if (String(runtimeToken || '').trim()) {
    return 'token';
  }
  if (String(LOGIN_USERNAME || '').trim() && String(LOGIN_PASSWORD || '').trim()) {
    return 'password';
  }
  return 'missing';
}

async function parseJsonResponse(response, fallbackMessage) {
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    throw new Error(fallbackMessage);
  }
  return payload;
}

function printStartupSummary() {
  console.log('[ui-runner] startup config');
  console.log(`[ui-runner] server=${runtimeServer}`);
  console.log(
    `[ui-runner] project_id=${runtimeProjectId || 0} plan_id=${runtimePlanId || 0} ` +
    `run_id=${preferredRunId || 0} run_ids=${preferredRunIds.join(',') || '(empty)'} any_project=${runtimeAnyProject}`,
  );
  console.log(`[ui-runner] browser=${DEFAULT_BROWSER} headless=${DEFAULT_HEADLESS}`);
  console.log(`[ui-runner] artifact_upload_concurrency=${ARTIFACT_UPLOAD_CONCURRENCY} artifact_upload_attempts=${ARTIFACT_UPLOAD_ATTEMPTS}`);
  console.log(`[ui-runner] auth_mode=${getAuthMode()} token=${maskToken(runtimeToken) || '(empty)'}`);
  console.log(`[ui-runner] bootstrap=${Object.keys(bootstrapConfig).length ? BOOTSTRAP_PATH : '(missing)'}`);
}

function validateStartupConfig() {
  const problems = [];
  if (!runtimeServer) {
    problems.push('UI_RUNNER_SERVER 未配置');
  }
  if (!runtimeProjectId && !preferredRunId && !preferredRunIds.length && !runtimeAnyProject) {
    problems.push('UI_RUNNER_PROJECT_ID / UI_RUNNER_RUN_ID 未配置，且未启用 UI_RUNNER_ANY_PROJECT');
  }

  const authMode = getAuthMode();
  if (authMode === 'missing') {
    problems.push('账号没填：请配置 UI_RUNNER_TOKEN，或同时配置 UI_RUNNER_USERNAME 和 UI_RUNNER_PASSWORD');
  } else if (authMode === 'password') {
    if (!String(LOGIN_USERNAME || '').trim()) {
      problems.push('账号没填：UI_RUNNER_USERNAME 为空');
    }
    if (!String(LOGIN_PASSWORD || '').trim()) {
      problems.push('账号没填：UI_RUNNER_PASSWORD 为空');
    }
  }

  if (problems.length) {
    throw new Error(problems.join('；'));
  }
}

function hasRunnableConfig() {
  if (!runtimeServer) {
    return false;
  }
  if (!runtimeProjectId && !preferredRunId && !preferredRunIds.length && !runtimeAnyProject) {
    return false;
  }
  return getAuthMode() !== 'missing';
}

async function refreshBootstrapConfig() {
  let nextBootstrap = {};
  try {
    const bootstrapText = await fs.readFile(BOOTSTRAP_PATH, 'utf-8');
    nextBootstrap = JSON.parse(bootstrapText);
  } catch {
    nextBootstrap = {};
  }

  bootstrapConfig = nextBootstrap;

  if (!EXPLICIT_SERVER) {
    runtimeServer = normalizeServerBase(nextBootstrap.server || runtimeServer || 'http://127.0.0.1:7777');
  }
  if (!EXPLICIT_TOKEN) {
    runtimeToken = String(nextBootstrap.token || runtimeToken || '').trim();
  }
  if (!EXPLICIT_PROJECT_ID) {
    runtimeProjectId = Number(nextBootstrap.project_id || runtimeProjectId || 0);
  }
  if (!EXPLICIT_PLAN_ID) {
    runtimePlanId = Number(nextBootstrap.plan_id || runtimePlanId || 0);
  }
  if (!EXPLICIT_RUN_ID) {
    const nextRunIds = normalizeArray(nextBootstrap.run_ids)
      .map((item) => Number(item || 0))
      .filter((item) => item > 0);
    const nextRunId = Number(nextBootstrap.run_id || nextRunIds[0] || 0);
    if (nextRunId && nextRunId !== preferredRunId) {
      console.log(`[ui-runner] bootstrap switched target run_id=${nextRunId}`);
    }
    preferredRunId = nextRunId || 0;
    preferredRunIds = nextRunIds.length ? nextRunIds : (preferredRunId ? [preferredRunId] : []);
  }
  if (EXPLICIT_ANY_PROJECT === undefined) {
    runtimeAnyProject = parseBool(nextBootstrap.any_project, runtimeAnyProject);
  }
}

async function requestJson(url, method, body) {
  const response = await fetch(url, {
    method,
    headers: {
      'Content-Type': 'application/json',
      token: runtimeToken,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await parseJsonResponse(response, `request failed: ${url}, response is not valid JSON`);
  if (data?.code !== 0) {
    throw new Error(data?.msg || `request failed: ${url}`);
  }
  return data?.data;
}

async function loginAndGetToken() {
  if (!LOGIN_USERNAME || !LOGIN_PASSWORD) {
    throw new Error('账号没填：请配置 UI_RUNNER_TOKEN，或同时配置 UI_RUNNER_USERNAME 和 UI_RUNNER_PASSWORD');
  }

  const response = await fetch(`${runtimeServer}/auth/login`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      username: LOGIN_USERNAME,
      password: LOGIN_PASSWORD,
    }),
  });
  const payload = await parseJsonResponse(response, '登录失败：/auth/login 响应不是有效 JSON');
  if (payload?.code !== 0) {
    throw new Error(`登录失败：${payload?.msg || 'ui runner login failed'}`);
  }

  const token = String(payload?.data?.token || '').trim();
  if (!token) {
    throw new Error('登录失败：登录接口返回成功，但 token 为空');
  }
  runtimeToken = token;
  console.log(`[ui-runner] authenticated as ${LOGIN_USERNAME}`);
}

function normalizeModelFamily(modelName) {
  const value = String(modelName || '').trim().toLowerCase();
  if (!value) {
    return '';
  }
  if (value.startsWith('qwen3-vl')) {
    return 'qwen3-vl';
  }
  if (value.startsWith('qwen-vl') || value.includes('qwen2.5-vl')) {
    return 'qwen2.5-vl';
  }
  if (value.startsWith('qwen3.6')) {
    return 'qwen3.6';
  }
  if (value.startsWith('qwen3.5')) {
    return 'qwen3.5';
  }
  if (value.startsWith('qwen3')) {
    return 'qwen3';
  }
  if (value.startsWith('gemini')) {
    return 'gemini';
  }
  if (value.startsWith('gpt-5')) {
    return 'gpt-5';
  }
  if (value.includes('doubao')) {
    return 'doubao-seed';
  }
  return '';
}

function validateMidsceneModelSupport(provider, model, family) {
  if (family) {
    return;
  }
  throw new Error(
    `当前 Midscene 版本不支持该模型作为 UI 自动化模型: provider=${provider || 'unknown'}, model=${model || 'unknown'}。` +
    ' 请在平台 AI 模型配置中切换到 Midscene 支持的视觉模型，例如 qwen3-vl、qwen2.5-vl、doubao-seed、gemini 或 gpt-5。',
  );
}

function resolveActiveAiConfig(payload) {
  if (!payload || typeof payload !== 'object') {
    return null;
  }

  if (payload.api_key && payload.base_url && payload.model) {
    return payload;
  }

  if (payload.ai_model && typeof payload.ai_model === 'object') {
    const nested = payload.ai_model;
    if (nested.api_key && nested.base_url && nested.model) {
      return nested;
    }
  }

  const activeProvider = String(payload.active_provider || payload.provider || '').trim();
  const providers = Array.isArray(payload.providers) ? payload.providers : [];
  const activeModelId = String(payload.active_model_id || '').trim();

  if (providers.length) {
    const matchedProvider = providers.find((item) => String(item?.id || '').trim() === activeModelId) || providers.find((item) => item?.enabled);
    if (matchedProvider) {
      const apiKey = String(matchedProvider.api_key || '').trim();
      const baseUrl = String(matchedProvider.base_url || '').trim();
      const model = String(matchedProvider.model || '').trim();
      if (apiKey && baseUrl && model) {
        return {
          provider: matchedProvider.provider_type || matchedProvider.provider || 'custom',
          api_key: apiKey,
          base_url: baseUrl,
          model,
          wire_api: matchedProvider.wire_api,
        };
      }
    }
  }

  const models = payload.models && typeof payload.models === 'object' ? payload.models : {};
  const orderedProviders = [activeProvider, ...Object.keys(models)].filter(Boolean);

  for (const providerKey of orderedProviders) {
    const modelConfig = models[providerKey];
    if (!modelConfig || typeof modelConfig !== 'object') {
      continue;
    }
    const apiKey = String(modelConfig.api_key || '').trim();
    const baseUrl = String(modelConfig.base_url || '').trim();
    const model = String(modelConfig.model || '').trim();
    if (apiKey && baseUrl && model) {
      return {
        provider: providerKey,
        api_key: apiKey,
        base_url: baseUrl,
        model,
        wire_api: modelConfig.wire_api,
      };
    }
  }
  return null;
}

async function hydrateMidsceneModelEnv() {
  let activeConfig = resolveActiveAiConfig(bootstrapConfig);

  if (!activeConfig) {
    const response = await fetch(`${runtimeServer}/config/ai-model/config`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
        token: runtimeToken,
      },
    });
    const payload = await parseJsonResponse(response, '模型配置读取失败：/config/ai-model/config 响应不是有效 JSON');
    if (payload?.code !== 0) {
      throw new Error(`模型配置读取失败：${payload?.msg || 'load ai model config failed'}`);
    }
    activeConfig = resolveActiveAiConfig(payload?.data);
  }
  if (!activeConfig) {
    throw new Error('模型配置读取失败：没有找到当前启用的 AI 模型配置');
  }

  const provider = String(activeConfig.provider || 'custom').trim().toLowerCase();
  const apiKey = String(activeConfig.api_key || '').trim();
  const model = String(activeConfig.model || '').trim();
  const baseUrl = String(activeConfig.base_url || '').trim().replace(/\/$/, '');
  const inferredFamily = normalizeModelFamily(model);
  const configuredFamily = String(activeConfig.family || process.env.MIDSCENE_MODEL_FAMILY || '').trim();
  const family = configuredFamily && configuredFamily !== 'openai' ? configuredFamily : inferredFamily;
  validateMidsceneModelSupport(provider, model, family);

  process.env.OPENAI_API_KEY = apiKey;
  process.env.OPENAI_BASE_URL = baseUrl;
  process.env.OPENAI_MODEL_NAME = model;
  process.env.MIDSCENE_OPENAI_API_KEY = apiKey;
  process.env.MIDSCENE_OPENAI_BASE_URL = baseUrl;
  process.env.MIDSCENE_OPENAI_MODEL_NAME = model;
  process.env.MIDSCENE_MODEL_PROVIDER = provider || 'custom';
  process.env.MIDSCENE_MODEL_NAME = model;
  process.env.MIDSCENE_MODEL_FAMILY = family;

  console.log(
    `[ui-runner] midscene model synced provider=${provider || 'custom'} model=${model} base_url=${baseUrl}`,
  );
  midsceneModelReady = true;
}

async function claimTask() {
  if (!runtimeProjectId && !preferredRunId && !preferredRunIds.length && !runtimeAnyProject) {
    throw new Error('UI_RUNNER_PROJECT_ID / UI_RUNNER_RUN_ID / UI_RUNNER_ANY_PROJECT is required');
  }
  try {
    const targetRunIds = preferredRunIds.length ? preferredRunIds : (preferredRunId ? [preferredRunId] : []);
    for (const targetRunId of targetRunIds) {
      const task = await requestJson(`${runtimeServer}/ui-test/runner/claim`, 'POST', {
        project_id: runtimeProjectId,
        plan_id: runtimePlanId || undefined,
        run_id: targetRunId,
      });
      if (task) {
        preferredRunIds = preferredRunIds.filter((item) => Number(item) !== Number(targetRunId));
        preferredRunId = preferredRunIds[0] || 0;
        return task;
      }
      console.log(`[ui-runner] target run_id=${targetRunId} unavailable, try next target`);
    }
    preferredRunIds = [];
    preferredRunId = 0;
    return await requestJson(`${runtimeServer}/ui-test/runner/claim`, 'POST', {
      project_id: runtimeProjectId,
      plan_id: runtimePlanId || undefined,
      any_project: runtimeAnyProject,
    });
  } catch (error) {
    throw new Error(`任务领取失败：${String(error?.message || error)}`);
  }
}

async function saveStep(step) {
  return requestJson(`${runtimeServer}/ui-test/runner/step/save`, 'POST', step);
}

async function saveRun(payload) {
  return requestJson(`${runtimeServer}/ui-test/runner/run/save`, 'POST', payload);
}

async function getRunStatus(runId) {
  return requestJson(`${runtimeServer}/ui-test/runner/run/status?run_id=${encodeURIComponent(String(runId || 0))}`, 'GET');
}

async function assertRunNotCancelled(run, phase = '') {
  const statusPayload = await getRunStatus(run.id);
  if (statusPayload?.cancelled || statusPayload?.status === 'cancelled') {
    const suffix = phase ? `（${phase}）` : '';
    throw new RunCancelledError(`UI测试执行已被手动停止${suffix}`);
  }
  return statusPayload;
}

async function uploadArtifact(runId, objectKey, filePath, contentType = 'application/octet-stream') {
  let lastError = null;
  const attempts = ARTIFACT_UPLOAD_ATTEMPTS;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const form = new FormData();
      const buffer = await fs.readFile(filePath);
      const filename = path.basename(filePath);
      form.append('run_id', String(runId));
      form.append('object_key', objectKey);
      form.append('file', new Blob([buffer], { type: contentType }), filename);

      const response = await fetch(`${runtimeServer}/ui-test/runner/artifact/upload`, {
        method: 'POST',
        headers: {
          token: runtimeToken,
        },
        body: form,
      });
      let data = null;
      try {
        data = await response.json();
      } catch {
        data = null;
      }
      if (!response.ok || data?.code !== 0) {
        throw new Error(data?.msg || `artifact upload failed: ${objectKey}, http=${response.status}`);
      }
      return data?.data;
    } catch (error) {
      lastError = error;
      if (attempt < attempts) {
        await sleep(800 * attempt);
      }
    }
  }
  throw lastError || new Error(`artifact upload failed: ${objectKey}`);
}

async function safeUploadArtifact(runId, objectKey, filePath, contentType, warnings = [], label = '') {
  if (!objectKey || !filePath) {
    return null;
  }
  try {
    return await uploadArtifact(runId, objectKey, filePath, contentType);
  } catch (error) {
    const warning = {
      label: label || path.basename(filePath),
      object_key: objectKey,
      local_path: filePath,
      message: String(error?.message || error),
    };
    warnings.push(warning);
    console.warn(`[ui-runner] artifact upload failed: ${objectKey}, local=${filePath}, error=${warning.message}`);
    return null;
  }
}

class ArtifactUploadQueue {
  constructor(limit = 3) {
    this.limit = Math.max(1, Number(limit || 1));
    this.running = 0;
    this.queue = [];
    this.pending = new Set();
    this.cancelled = false;
    this.cancelReason = '';
  }

  enqueue(taskFactory) {
    if (this.cancelled) {
      return Promise.resolve(null);
    }
    let job;
    job = new Promise((resolve) => {
      const runTask = async () => {
        this.running += 1;
        try {
          resolve(await taskFactory());
        } catch (error) {
          console.warn(`[ui-runner] artifact upload task failed: ${String(error?.message || error)}`);
          resolve(null);
        } finally {
          this.running -= 1;
          this.pending.delete(job);
          this.pump();
        }
      };
      this.queue.push({ runTask, resolve, job: () => job });
    });
    this.pending.add(job);
    this.pump();
    return job;
  }

  cancel(reason = 'cancelled') {
    this.cancelled = true;
    this.cancelReason = reason;
    const queued = this.queue.splice(0);
    queued.forEach((item) => {
      try {
        item.resolve(null);
        this.pending.delete(item.job());
      } catch {}
    });
  }

  pump() {
    if (this.cancelled) {
      return;
    }
    while (this.running < this.limit && this.queue.length > 0) {
      const item = this.queue.shift();
      item.runTask();
    }
  }

  async drain() {
    while (this.pending.size > 0) {
      await Promise.allSettled([...this.pending]);
    }
  }
}

function enqueueUploadArtifact(uploadQueue, runId, objectKey, filePath, contentType, runWarnings = [], label = '', localWarnings = []) {
  const task = async () => {
    const beforeCount = localWarnings.length;
    const result = await safeUploadArtifact(runId, objectKey, filePath, contentType, localWarnings, label);
    const nextWarnings = localWarnings.slice(beforeCount);
    if (nextWarnings.length && runWarnings !== localWarnings) {
      runWarnings.push(...nextWarnings);
    }
    return result;
  };
  if (uploadQueue) {
    return uploadQueue.enqueue(task);
  }
  return task();
}

async function ensureDir(dirPath) {
  await fs.mkdir(dirPath, { recursive: true });
}

function buildLocalRunDir(run) {
  return path.resolve(process.cwd(), 'runtime', String(run.project_id), String(run.plan_id), String(run.id));
}

function summarizeCaseResults(caseResults = []) {
  return {
    success_case_count: caseResults.filter((item) => item.status === 'success').length,
    failed_case_count: caseResults.filter((item) => item.status === 'failed').length,
    skipped_case_count: caseResults.filter((item) => item.status === 'skipped').length,
  };
}

async function markRunUploading(run, localRunDir, startedAt, caseResults, artifactWarnings, extra = {}) {
  const counts = summarizeCaseResults(caseResults);
  try {
    await saveRun({
      run_id: run.id,
      status: 'uploading',
      error_message: extra.error_message || '',
      result_payload: {
        status: 'uploading',
        execution_status: extra.execution_status || 'success',
        artifact_phase: 'uploading',
        message: extra.message || '步骤执行已完成，正在生成并上传截图、录屏和报告产物。',
        elapsed_ms: Date.now() - startedAt,
        local_run_dir: localRunDir,
        case_count: caseResults.length,
        case_results: caseResults,
        ...counts,
        artifact_warnings: artifactWarnings,
      },
      video_path: run.video_path,
      report_path: run.report_path,
      result_json_path: run.result_json_path,
      screenshot_dir: run.screenshot_dir,
      artifact_prefix: run.artifact_prefix,
    });
  } catch (error) {
    console.warn(`[ui-runner] save uploading status failed: ${String(error?.message || error)}`);
  }
}

function buildRuntimeVariables(dsl = {}, extracted = {}, runtimeContext = {}) {
  const normalizedDsl = normalizePlainObject(dsl);
  const vars = {};
  const sceneConfig = normalizePlainObject(normalizedDsl.scene_config);
  Object.entries(sceneConfig).forEach(([key, value]) => {
    if (value !== null && value !== undefined && String(value).trim() !== '') {
      vars[String(key)] = value;
    }
  });
  Object.entries(runtimeContext || {}).forEach(([key, value]) => {
    if (value !== null && value !== undefined && String(value).trim() !== '') {
      vars[String(key)] = value;
    }
  });
  Object.entries(extracted || {}).forEach(([key, value]) => {
    vars[String(key)] = value;
  });
  const entryUrl = pickTemplateSource(normalizedDsl.entry_url, normalizedDsl.entryUrl, sceneConfig['页面入口']);
  if (entryUrl) {
    vars.entry_url = entryUrl;
    vars.entryUrl = entryUrl;
    if (!vars['页面入口'] || hasUnresolvedTemplate(String(vars['页面入口']))) {
      vars['页面入口'] = entryUrl;
    }
  }
  const baseUrl = pickTemplateSource(normalizedDsl.base_url, normalizedDsl.baseUrl, sceneConfig['基础地址']);
  if (baseUrl) {
    vars.base_url = baseUrl;
    vars.baseUrl = baseUrl;
    if (!vars['基础地址'] || hasUnresolvedTemplate(String(vars['基础地址']))) {
      vars['基础地址'] = baseUrl;
    }
  }
  for (let index = 0; index < 3; index += 1) {
    Object.keys(vars).forEach((key) => {
      const current = vars[key];
      if (typeof current === 'string' && hasUnresolvedTemplate(current)) {
        vars[key] = resolveTemplateString(current, vars);
      }
    });
  }
  return vars;
}

function resolveTemplateString(value, variables = {}) {
  const resolveKey = (rawKey) => {
    const key = String(rawKey || '').trim();
    if (!key) {
      return '';
    }
    const matched = Object.prototype.hasOwnProperty.call(variables, key) ? variables[key] : '';
    return matched === null || matched === undefined ? '' : String(matched);
  };
  return String(value ?? '')
    .replace(/\$\{([^}]+)\}/g, (_, rawKey) => resolveKey(rawKey))
    .replace(/\{\{([^}]+)\}\}/g, (_, rawKey) => resolveKey(rawKey));
}

function resolveDslValue(value, variables = {}) {
  if (typeof value === 'string') {
    return resolveTemplateString(value, variables);
  }
  if (Array.isArray(value)) {
    return value.map((item) => resolveDslValue(item, variables));
  }
  if (isPlainObject(value)) {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, resolveDslValue(item, variables)]),
    );
  }
  return value;
}

function buildTemplateVariables(dsl = {}, runtimeVariables = {}) {
  const normalizedDsl = normalizePlainObject(dsl);
  const sceneConfig = normalizePlainObject(normalizedDsl.scene_config);
  const vars = {
    ...sceneConfig,
    ...(runtimeVariables || {}),
  };
  const entryUrl = pickTemplateSource(
    normalizedDsl.entry_url,
    normalizedDsl.entryUrl,
    vars.entry_url,
    vars.entryUrl,
    vars['页面入口'],
  );
  if (entryUrl) {
    vars.entry_url = entryUrl;
    vars.entryUrl = entryUrl;
    vars['页面入口'] = entryUrl;
  }
  const baseUrl = pickTemplateSource(
    normalizedDsl.base_url,
    normalizedDsl.baseUrl,
    vars.base_url,
    vars.baseUrl,
    vars['基础地址'],
  );
  if (baseUrl) {
    vars.base_url = baseUrl;
    vars.baseUrl = baseUrl;
    vars['基础地址'] = baseUrl;
  }
  return vars;
}

function resolveEntryUrl(dsl = {}, runtimeVariables = {}) {
  const normalizedDsl = normalizePlainObject(dsl);
  const sceneConfig = normalizePlainObject(normalizedDsl.scene_config);
  const templateVariables = buildTemplateVariables(normalizedDsl, runtimeVariables);
  const candidates = [
    normalizedDsl.entry_url,
    normalizedDsl.entryUrl,
    sceneConfig['页面入口'],
    runtimeVariables.entry_url,
    runtimeVariables.entryUrl,
    runtimeVariables['页面入口'],
  ];
  for (const candidate of candidates) {
    const resolved = resolveTemplateString(candidate || '', templateVariables).trim();
    if (resolved && !hasUnresolvedTemplate(resolved)) {
      return resolved;
    }
  }
  return '';
}

function normalizeGotoUrl(value, label, baseUrl = '') {
  const url = String(value || '').trim();
  if (!url) {
    throw new Error(`${label}缺少可用 URL`);
  }
  if (hasUnresolvedTemplate(url)) {
    throw new Error(`${label}仍包含未解析变量：${url}`);
  }
  if (/^[a-zA-Z][a-zA-Z\d+\-.]*:\/\//.test(url)) {
    try {
      new URL(url);
    } catch {
      throw new Error(`${label}不是有效 URL：${url}`);
    }
    return url;
  }
  const baseValue = String(baseUrl || '').trim().replace(/\/$/, '');
  if (baseValue) {
    const normalizedPath = url.startsWith('/') ? url : `/${url}`;
    const merged = `${baseValue}${normalizedPath}`;
    try {
      new URL(merged);
    } catch {
      throw new Error(`${label}拼接基础地址后不是有效 URL：${merged}`);
    }
    return merged;
  }
  try {
    new URL(url);
  } catch {
    throw new Error(`${label}不是有效 URL：${url}`);
  }
  return url;
}

function getBrowserLauncher(browserName) {
  return browserMap[browserName] || chromium;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

async function settlePage(page, timeoutMs = 400) {
  const safe = async (handler) => {
    try {
      await handler();
    } catch {}
  };
  await safe(() => page.waitForLoadState('domcontentloaded', { timeout: timeoutMs }));
  await safe(() => page.evaluate(async () => {
    if (document.fonts?.ready) {
      try {
        await document.fonts.ready;
      } catch {}
    }
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }));
  await safe(() => page.waitForTimeout(80));
}

async function clearPageSelection(page) {
  try {
    await page.evaluate(() => {
      try {
        const selection = window.getSelection?.();
        selection?.removeAllRanges?.();
      } catch {}
      try {
        if (document.activeElement instanceof HTMLElement && typeof document.activeElement.blur === 'function') {
          document.activeElement.blur();
        }
      } catch {}
    });
  } catch {}
}

async function capturePageScreenshot(page, targetPath, options = {}) {
  const {
    fullPage = false,
    settle = true,
  } = options;
  if (settle) {
    await settlePage(page);
  }
  await clearPageSelection(page);
  try {
    await page.screenshot({ path: targetPath, fullPage, animations: 'disabled' });
  } catch (error) {
    await page.locator('body').screenshot({ path: targetPath, animations: 'disabled' });
  }
  await clearPageSelection(page);
}

const retryableStepTypes = new Set([
  'open',
  'click',
  'input',
  'select',
  'wait_exists',
  'wait_not_exists',
  'assert_exists',
  'assert_not_exists',
  'assert_text',
  'extract_text',
]);

function isSensitiveInputStep(step = {}) {
  const source = [
    step.target,
    step.raw,
    step.name,
    step.save_as,
  ].map((item) => String(item || '').toLowerCase()).join(' ');
  return /(密码|口令|密钥|token|secret|password|passwd|pwd)/i.test(source);
}

function maskStepPayload(step = {}) {
  if (String(step.type || '') !== 'input' || !isSensitiveInputStep(step)) {
    return step;
  }
  return {
    ...step,
    value: '******',
  };
}

async function withActionRetry(page, action, options = {}) {
  const {
    attempts = 2,
    label = '步骤动作',
    beforeAttempt,
  } = options;
  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await settlePage(page, 500);
      if (beforeAttempt) {
        await beforeAttempt(attempt);
      }
      const value = await action(attempt);
      await settlePage(page, 500);
      return { attempts: attempt, value };
    } catch (error) {
      lastError = error;
      if (attempt >= attempts) {
        break;
      }
      await page.waitForTimeout(250 * attempt);
    }
  }
  const message = String(lastError?.message || lastError || '未知错误');
  const retryError = new Error(`${label}失败，已重试 ${attempts} 次：${message}`);
  retryError.stack = lastError?.stack || retryError.stack;
  retryError.__argus_retry_attempts = attempts;
  throw retryError;
}

function escapeRegExp(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

async function resolveVisibleLocator(candidates = []) {
  for (const locator of candidates) {
    try {
      if (await locator.count()) {
        const first = locator.first();
        await first.waitFor({ state: 'visible', timeout: 1200 });
        return first;
      }
    } catch {}
  }
  return null;
}

async function findNativeInputLocator(page, target) {
  const pattern = new RegExp(escapeRegExp(target), 'i');
  return resolveVisibleLocator([
    page.getByLabel(pattern),
    page.getByPlaceholder(pattern),
    page.getByRole('textbox', { name: pattern }),
    page.getByRole('searchbox', { name: pattern }),
    page.locator(`input[placeholder*="${target}"], textarea[placeholder*="${target}"]`),
  ]);
}

async function findNativeClickLocator(page, target) {
  const pattern = new RegExp(escapeRegExp(target), 'i');
  return resolveVisibleLocator([
    page.getByRole('button', { name: pattern }),
    page.getByRole('link', { name: pattern }),
    page.getByRole('tab', { name: pattern }),
    page.getByText(pattern),
  ]);
}

async function findNativeSelectLocator(page, target) {
  const pattern = new RegExp(escapeRegExp(target), 'i');
  return resolveVisibleLocator([
    page.getByLabel(pattern),
    page.getByPlaceholder(pattern),
    page.getByRole('combobox', { name: pattern }),
    page.locator('select'),
  ]);
}

async function readLocatorValue(locator) {
  return locator.evaluate((node) => {
    if (!node) {
      return '';
    }
    if ('value' in node) {
      return String(node.value || '');
    }
    return String(node.textContent || '');
  });
}

async function performNativeInput(page, step = {}) {
  const target = String(step.target || '').trim();
  const value = String(step.value ?? '');
  const locator = await findNativeInputLocator(page, target);
  if (!locator) {
    throw new Error(`未找到匹配输入框：${target}`);
  }
  await clearPageSelection(page);
  await locator.click({ timeout: 1500 });
  try {
    await locator.fill(value, { timeout: 1500 });
  } catch {
    await locator.press('Control+A');
    await locator.type(value, { delay: 20 });
  }
  const actualValue = await readLocatorValue(locator);
  if (isSensitiveInputStep(step)) {
    if (!String(actualValue || '').trim()) {
      throw new Error(`输入框写入后仍为空：${target}`);
    }
  } else if (String(actualValue || '').trim() !== value.trim()) {
    throw new Error(`输入框值校验失败：${target}`);
  }
}

async function performNativeClick(page, step = {}) {
  const target = String(step.target || '').trim();
  const locator = await findNativeClickLocator(page, target);
  if (!locator) {
    throw new Error(`未找到匹配点击目标：${target}`);
  }
  await clearPageSelection(page);
  await locator.click({ timeout: 1500 });
}

async function performNativeSelect(page, step = {}) {
  const target = String(step.target || '').trim();
  const value = String(step.value ?? '').trim();
  const locator = await findNativeSelectLocator(page, target);
  if (!locator) {
    throw new Error(`未找到匹配选择控件：${target}`);
  }
  const tagName = await locator.evaluate((node) => String(node?.tagName || '').toLowerCase()).catch(() => '');
  if (tagName === 'select') {
    try {
      await locator.selectOption({ label: value }, { timeout: 1500 });
    } catch {
      await locator.selectOption(value, { timeout: 1500 });
    }
    return;
  }
  await locator.click({ timeout: 1500 });
  const optionPattern = new RegExp(escapeRegExp(value), 'i');
  const option = await resolveVisibleLocator([
    page.getByRole('option', { name: optionPattern }),
    page.getByText(optionPattern),
  ]);
  if (!option) {
    throw new Error(`未找到可选项：${value}`);
  }
  await option.click({ timeout: 1500 });
}

async function withPlaywrightFirst(page, nativeAction, aiAction, options = {}) {
  const nativeAttempts = Number(options.nativeAttempts || 1);
  const aiAttempts = Number(options.aiAttempts || 2);
  const label = String(options.label || '步骤动作');
  try {
    const nativeMeta = await withActionRetry(page, nativeAction, {
      attempts: nativeAttempts,
      label: `${label}[Playwright]`,
    });
    return {
      ...nativeMeta,
      strategy: 'playwright',
      used_ai: false,
    };
  } catch (nativeError) {
    const aiMeta = await withActionRetry(page, aiAction, {
      attempts: aiAttempts,
      label: `${label}[AI兜底]`,
    });
    return {
      ...aiMeta,
      strategy: 'midscene',
      used_ai: true,
      fallback_error: String(nativeError?.message || nativeError || ''),
    };
  }
}

async function stableInput(agent, page, step = {}) {
  const target = String(step.target || '').trim();
  const value = String(step.value ?? '');
  if (!target) {
    throw new Error('input 步骤缺少目标输入框');
  }
  if (!value) {
    throw new Error(`input 步骤缺少输入值：${target}`);
  }
  return withPlaywrightFirst(
    page,
    () => performNativeInput(page, step),
    async () => {
      const sensitive = isSensitiveInputStep(step);
      const verifyPrompt = sensitive
        ? `${target}输入框已经填写了内容`
        : `${target}输入框的值已经是${value}`;
      await clearPageSelection(page);
      await agent.aiInput(value, target);
      await clearPageSelection(page);
      await page.waitForTimeout(120);
      await agent.aiAssert(verifyPrompt);
    },
    { label: `输入 ${target}`, nativeAttempts: 1, aiAttempts: 2 },
  );
}

function buildRunnerCases(run) {
  const runnerPayload = normalizePlainObject(run.runner_payload);
  const runnerCases = normalizeArray(runnerPayload.cases);
  if (runnerCases.length) {
    return runnerCases.map((rawItem, index) => {
      const item = normalizePlainObject(rawItem);
      const dsl = normalizePlainObject(item.dsl);
      return {
        case_index: Number(item.case_index || index + 1),
        case_ref_id: Number(item.case_ref_id || 0),
        file_title: String(item.file_title || ''),
        node_title: String(item.node_title || dsl.ui_case_title || `用例${index + 1}`),
        node_path: String(item.node_path || dsl.ui_case_path || ''),
        dsl,
      };
    });
  }
  const dsl = normalizePlainObject(runnerPayload.dsl);
  return [{
    case_index: 1,
    case_ref_id: Number(run.case_ref_id || 0),
    file_title: String(runnerPayload.file_title || ''),
    node_title: String(runnerPayload.node_title || dsl.ui_case_title || run.run_name || '试运行用例'),
    node_path: String(runnerPayload.node_path || dsl.ui_case_path || ''),
    dsl,
  }];
}

function normalizeOpenStep(step = {}, runtimeVariables = {}, dsl = {}) {
  if (String(step.type || '') !== 'open') {
    return step;
  }
  const nextStep = { ...step };
  const templateVariables = buildTemplateVariables(dsl, runtimeVariables);
  if (nextStep.value) {
    nextStep.value = resolveTemplateString(nextStep.value, templateVariables).trim();
  }
  const resolvedValue = String(nextStep.value || '').trim();
  if (!resolvedValue || hasUnresolvedTemplate(resolvedValue)) {
    const fallbackUrl = resolveEntryUrl(dsl, templateVariables);
    if (fallbackUrl) {
      nextStep.value = fallbackUrl;
    }
  }
  return nextStep;
}

function buildStepDisplayName(step, caseMeta = {}, multiCase = false) {
  const rawName = String(step?.raw || step?.type || '').trim() || '未命名步骤';
  if (!multiCase) {
    return rawName;
  }
  const caseTitle = String(caseMeta.case_title || caseMeta.node_title || `用例${caseMeta.case_index || 0}`).trim();
  return `[${caseTitle}] ${rawName}`;
}

function buildSkippedCaseResult(caseItem = {}, caseIndex = 0, reason = '') {
  const dsl = normalizePlainObject(caseItem.dsl);
  const steps = Array.isArray(dsl.steps) ? dsl.steps : [];
  return {
    case_index: caseItem.case_index || (caseIndex + 1),
    case_ref_id: caseItem.case_ref_id || 0,
    file_title: caseItem.file_title || '',
    case_title: String(caseItem.node_title || dsl.ui_case_title || `用例${caseIndex + 1}`),
    node_path: String(caseItem.node_path || dsl.ui_case_path || ''),
    status: 'skipped',
    step_count: steps.length,
    success_step_count: 0,
    failed_step_count: 0,
    skipped_step_count: steps.length,
    elapsed_ms: 0,
    error_message: reason,
  };
}

function buildHtmlReport(run, steps, summary, error, caseResults = []) {
  const caseRows = caseResults.map((item) => {
    const statusColor = item.status === 'success' ? '#15803d' : item.status === 'skipped' ? '#b45309' : '#b91c1c';
    return `
      <tr>
        <td>${Number(item.case_index || 0)}</td>
        <td>${escapeHtml(item.case_title || '-')}</td>
        <td>${escapeHtml(item.node_path || '-')}</td>
        <td style="color:${statusColor};font-weight:600;">${escapeHtml(item.status || '-')}</td>
        <td>${Number(item.step_count || 0)}</td>
        <td>${Number(item.success_step_count || 0)}</td>
        <td>${Number(item.failed_step_count || 0)}</td>
        <td>${Number(item.skipped_step_count || 0)}</td>
        <td>${Number(item.elapsed_ms || 0)}</td>
        <td><pre>${escapeHtml(item.error_message || '')}</pre></td>
      </tr>
    `;
  }).join('');

  const rows = steps.map((step) => {
    const statusColor = step.status === 'success' ? '#15803d' : step.status === 'failed' ? '#b91c1c' : '#b45309';
    return `
      <tr>
        <td>${step.step_index}</td>
        <td>${escapeHtml(step.case_title || '-')}</td>
        <td>${escapeHtml(step.step_name || step.step_type || '-')}</td>
        <td>${escapeHtml(step.step_type || '-')}</td>
        <td style="color:${statusColor};font-weight:600;">${escapeHtml(step.status || '-')}</td>
        <td>${Number(step.duration_ms || 0)}</td>
        <td>${escapeHtml(step.screenshot_path || '-')}</td>
        <td><pre>${escapeHtml(step.error_message || '')}</pre></td>
      </tr>
    `;
  }).join('');

  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Argus UI Run ${run.id}</title>
  <style>
    body{font-family:Arial,Helvetica,sans-serif;margin:24px;color:#0f172a;background:#f8fafc;}
    h1,h2{margin:0 0 12px;}
    .card{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin-bottom:16px;}
    .ok{color:#15803d}.fail{color:#b91c1c}.warn{color:#b45309}
    table{width:100%;border-collapse:collapse;background:#fff}
    th,td{border:1px solid #e2e8f0;padding:10px;vertical-align:top;text-align:left;font-size:12px}
    th{background:#f1f5f9}
    pre{margin:0;white-space:pre-wrap;word-break:break-word}
    .kv{margin:6px 0}
  </style>
</head>
<body>
  <div class="card">
    <h1>Argus UI Run ${run.id}</h1>
    <div class="kv"><strong>Status:</strong> <span class="${summary.status === 'success' ? 'ok' : 'fail'}">${escapeHtml(summary.status || '-')}</span></div>
    <div class="kv"><strong>Project:</strong> ${Number(run.project_id || 0)}</div>
    <div class="kv"><strong>Plan:</strong> ${Number(run.plan_id || 0)}</div>
    <div class="kv"><strong>Case Count:</strong> ${Number(caseResults.length || 0)}</div>
    <div class="kv"><strong>Artifact Prefix:</strong> ${escapeHtml(run.artifact_prefix || '-')}</div>
    <div class="kv"><strong>Started At:</strong> ${new Date().toISOString()}</div>
    <div class="kv"><strong>Summary:</strong> ${escapeHtml(summary.message || '')}</div>
  </div>
  ${error ? `<div class="card"><h2 class="fail">Error</h2><pre>${escapeHtml(error)}</pre></div>` : ''}
  ${caseResults.length ? `
  <div class="card">
    <h2>Cases</h2>
    <table>
      <thead>
        <tr><th>#</th><th>Case</th><th>Path</th><th>Status</th><th>Steps</th><th>Success</th><th>Failed</th><th>Skipped</th><th>Elapsed(ms)</th><th>Error</th></tr>
      </thead>
      <tbody>${caseRows}</tbody>
    </table>
  </div>` : ''}
  <div class="card">
    <h2>Steps</h2>
    <table>
      <thead>
        <tr><th>#</th><th>Case</th><th>Step</th><th>Type</th><th>Status</th><th>Duration(ms)</th><th>Screenshot</th><th>Error</th></tr>
      </thead>
      <tbody>${rows || '<tr><td colspan="8">No steps</td></tr>'}</tbody>
    </table>
  </div>
</body>
</html>`;
}

async function executeDslStep(agent, page, run, step, stepIndex, localRunDir, runtimeVariables, caseMeta = {}) {
  const screenshotDir = path.join(localRunDir, 'screenshots');
  await ensureDir(screenshotDir);
  const startedAt = Date.now();
  const multiCase = Boolean(caseMeta?.multi_case);
  const screenshotName = multiCase
    ? `case_${String(caseMeta.case_index || 0).padStart(2, '0')}_step_${String(caseMeta.case_step_index || 0).padStart(2, '0')}.png`
    : `step_${String(stepIndex).padStart(2, '0')}.png`;
  const screenshotPath = path.join(screenshotDir, screenshotName);
  const screenshotObjectKey = `${run.screenshot_dir}${screenshotName}`;
  const normalizedDsl = normalizePlainObject(caseMeta.dsl);
  const templateVariables = buildTemplateVariables(normalizedDsl, runtimeVariables);
  const currentBaseUrl = String(templateVariables.base_url || templateVariables.baseUrl || templateVariables['基础地址'] || '').trim();
  const resolvedStep = normalizeOpenStep(
    resolveDslValue(step, templateVariables),
    templateVariables,
    normalizedDsl,
  );
  const displayStepName = buildStepDisplayName(resolvedStep, caseMeta, multiCase);
  const requestPayload = {
    ...maskStepPayload(resolvedStep),
    case_index: Number(caseMeta.case_index || 0),
    case_ref_id: Number(caseMeta.case_ref_id || 0),
    case_title: String(caseMeta.case_title || ''),
    case_path: String(caseMeta.node_path || ''),
    case_step_index: Number(caseMeta.case_step_index || 0),
  };

  await saveStep({
    run_id: run.id,
    step_index: stepIndex,
    step_name: displayStepName,
    step_type: resolvedStep.type,
    status: 'running',
    request_payload: requestPayload,
  });

  let actionMeta = {
    attempts: 1,
    retryable: retryableStepTypes.has(String(resolvedStep.type || '')),
  };
  const artifactWarnings = Array.isArray(caseMeta?.artifact_warnings) ? caseMeta.artifact_warnings : [];
  const artifactUploadQueue = caseMeta?.artifact_upload_queue || null;

  try {
    switch (resolvedStep.type) {
      case 'open':
        if (!resolvedStep.value || hasUnresolvedTemplate(resolvedStep.value)) {
          throw new Error(`open 步骤缺少可用 URL，当前值=${String(resolvedStep.value || '(empty)')}`);
        }
        actionMeta = await withActionRetry(
          page,
          () => page.goto(normalizeGotoUrl(resolvedStep.value, 'open 步骤 URL', currentBaseUrl), { waitUntil: 'domcontentloaded' }),
          { attempts: 2, label: `打开 ${resolvedStep.value}` },
        );
        break;
      case 'click':
        actionMeta = await withPlaywrightFirst(
          page,
          () => performNativeClick(page, resolvedStep),
          () => agent.aiTap(resolvedStep.target),
          { label: `点击 ${resolvedStep.target || ''}`, nativeAttempts: 1, aiAttempts: 2 },
        );
        break;
      case 'input':
        actionMeta = await stableInput(agent, page, resolvedStep);
        break;
      case 'select':
        actionMeta = await withPlaywrightFirst(
          page,
          () => performNativeSelect(page, resolvedStep),
          () => agent.aiAct(`在${resolvedStep.target}中选择${resolvedStep.value}`),
          { label: `选择 ${resolvedStep.target || ''}`, nativeAttempts: 1, aiAttempts: 2 },
        );
        break;
      case 'wait_exists':
        actionMeta = await withActionRetry(
          page,
          () => agent.aiWaitFor(`${resolvedStep.target}已经出现`),
          { attempts: 2, label: `等待出现 ${resolvedStep.target || ''}` },
        );
        break;
      case 'wait_not_exists':
        actionMeta = await withActionRetry(
          page,
          () => agent.aiWaitFor(`${resolvedStep.target}已经消失`),
          { attempts: 2, label: `等待消失 ${resolvedStep.target || ''}` },
        );
        break;
      case 'assert_exists':
        actionMeta = await withActionRetry(
          page,
          () => agent.aiAssert(`${resolvedStep.target}存在`),
          { attempts: 2, label: `断言存在 ${resolvedStep.target || ''}` },
        );
        break;
      case 'assert_not_exists':
        actionMeta = await withActionRetry(
          page,
          () => agent.aiAssert(`${resolvedStep.target}不存在`),
          { attempts: 2, label: `断言不存在 ${resolvedStep.target || ''}` },
        );
        break;
      case 'assert_text':
        actionMeta = await withActionRetry(
          page,
          () => agent.aiAssert(`${resolvedStep.target}的文本是${resolvedStep.expected}`),
          { attempts: 2, label: `断言文本 ${resolvedStep.target || ''}` },
        );
        break;
      case 'extract_text': {
        actionMeta = await withActionRetry(
          page,
          () => agent.aiString(`读取${resolvedStep.target}的文本`),
          { attempts: 2, label: `提取文本 ${resolvedStep.target || ''}` },
        );
        const value = actionMeta.value;
        runtimeVariables[String(resolvedStep.save_as)] = value;
        if (caseMeta?.extracted_store && resolvedStep.save_as) {
          caseMeta.extracted_store[String(resolvedStep.save_as)] = value;
        }
        await page.evaluate(
          ([key, val]) => {
            window.__argus_ui_extracts = window.__argus_ui_extracts || {};
            window.__argus_ui_extracts[key] = val;
          },
          [resolvedStep.save_as, value],
        );
        break;
      }
      case 'screenshot':
        await capturePageScreenshot(page, screenshotPath, { fullPage: true, settle: true });
        break;
      default:
        throw new Error(`unsupported step type: ${resolvedStep.type}`);
    }

    if (resolvedStep.type !== 'screenshot') {
      await capturePageScreenshot(page, screenshotPath, { fullPage: false, settle: false });
    }
    const screenshotUploadWarnings = [];
    enqueueUploadArtifact(
      artifactUploadQueue,
      run.id,
      screenshotObjectKey,
      screenshotPath,
      'image/png',
      artifactWarnings,
      displayStepName,
      screenshotUploadWarnings,
    );

    const stepSummary = {
      step_index: stepIndex,
      case_index: Number(caseMeta.case_index || 0),
      case_ref_id: Number(caseMeta.case_ref_id || 0),
      case_title: String(caseMeta.case_title || ''),
      case_path: String(caseMeta.node_path || ''),
      step_name: displayStepName,
      step_type: resolvedStep.type,
      status: 'success',
      screenshot_path: screenshotObjectKey,
      duration_ms: Date.now() - startedAt,
      error_message: '',
      artifact_warnings: screenshotUploadWarnings,
      used_ai: Boolean(actionMeta.used_ai),
    };
    await saveStep({
      run_id: run.id,
      step_index: stepIndex,
      step_name: displayStepName,
      step_type: resolvedStep.type,
      status: 'success',
      screenshot_path: screenshotObjectKey,
      request_payload: requestPayload,
      result_payload: {
        ok: true,
        case_index: stepSummary.case_index,
        case_ref_id: stepSummary.case_ref_id,
        case_title: stepSummary.case_title,
        action_meta: {
          retryable: actionMeta.retryable !== false,
          attempts: Number(actionMeta.attempts || 1),
          strategy: String(actionMeta.strategy || 'playwright'),
          used_ai: Boolean(actionMeta.used_ai),
        },
        artifact_warnings: screenshotUploadWarnings,
        local_screenshot_path: screenshotPath,
      },
      duration_ms: stepSummary.duration_ms,
    });
    return stepSummary;
  } catch (error) {
    try {
      await capturePageScreenshot(page, screenshotPath);
    } catch {}
    const screenshotUploadWarnings = [];
    enqueueUploadArtifact(
      artifactUploadQueue,
      run.id,
      screenshotObjectKey,
      screenshotPath,
      'image/png',
      artifactWarnings,
      displayStepName,
      screenshotUploadWarnings,
    );
    const stepSummary = {
      step_index: stepIndex,
      case_index: Number(caseMeta.case_index || 0),
      case_ref_id: Number(caseMeta.case_ref_id || 0),
      case_title: String(caseMeta.case_title || ''),
      case_path: String(caseMeta.node_path || ''),
      step_name: displayStepName,
      step_type: resolvedStep.type,
      status: 'failed',
      screenshot_path: screenshotObjectKey,
      duration_ms: Date.now() - startedAt,
      error_message: String(error?.stack || error?.message || error),
      artifact_warnings: screenshotUploadWarnings,
      used_ai: Boolean(actionMeta.used_ai),
    };
    await saveStep({
      run_id: run.id,
      step_index: stepIndex,
      step_name: displayStepName,
      step_type: resolvedStep.type,
      status: 'failed',
      screenshot_path: screenshotObjectKey,
      request_payload: requestPayload,
      result_payload: {
        ok: false,
        message: String(error?.message || error),
        case_index: stepSummary.case_index,
        case_ref_id: stepSummary.case_ref_id,
        case_title: stepSummary.case_title,
        action_meta: {
          retryable: actionMeta.retryable !== false,
          attempts: Number(error?.__argus_retry_attempts || actionMeta.attempts || 1),
          strategy: String(actionMeta.strategy || 'playwright'),
          used_ai: Boolean(actionMeta.used_ai),
        },
        artifact_warnings: screenshotUploadWarnings,
        local_screenshot_path: screenshotPath,
      },
      error_message: stepSummary.error_message,
      duration_ms: stepSummary.duration_ms,
    });
    error.__argus_ui_step_summary = stepSummary;
    throw error;
  }
}

async function executeRun(run) {
  const runnerPayload = normalizePlainObject(run.runner_payload);
  const runnerConfig = normalizePlainObject(runnerPayload.runner_config);
  const runnerCases = buildRunnerCases(run);
  const primaryDsl = runnerCases[0]?.dsl || normalizePlainObject(runnerPayload.dsl);
  const browserName = primaryDsl.browser || run.browser || DEFAULT_BROWSER;
  const headless = typeof primaryDsl.headless === 'boolean' ? primaryDsl.headless : (run.headless ?? DEFAULT_HEADLESS);
  const launch = getBrowserLauncher(browserName);
  const localRunDir = buildLocalRunDir(run);
  const localVideoDir = path.join(localRunDir, 'videos');
  const localReportDir = path.join(localRunDir, 'reports');
  const localLogDir = path.join(localRunDir, 'logs');
  const localResultJsonPath = path.join(localLogDir, 'result.json');
  const localReportPath = path.join(localReportDir, 'report.html');
  await ensureDir(localVideoDir);
  await ensureDir(localReportDir);
  await ensureDir(localLogDir);

  const browser = await launch.launch({
    headless: Boolean(headless),
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    recordVideo: {
      dir: localVideoDir,
      size: { width: 1440, height: 900 },
    },
  });
  const page = await context.newPage();
  const agent = new PlaywrightAgent(page);
  const startedAt = Date.now();
  const executedSteps = [];
  const caseResults = [];
  const extractedVariables = {};
  const artifactWarnings = [];
  const artifactUploadQueue = new ArtifactUploadQueue(ARTIFACT_UPLOAD_CONCURRENCY);
  let globalStepIndex = 0;
  const multiCase = runnerCases.length > 1;
  const runtimeContext = {};
  const runtimeBaseUrl = pickTemplateSource(
    runnerPayload.base_url,
    runnerPayload.baseUrl,
    runnerConfig.base_url,
    runnerConfig.baseUrl,
    runnerPayload.page_url,
    runnerPayload.pageUrl,
  );
  const runtimeEntryUrl = pickTemplateSource(
    runnerPayload.entry_url,
    runnerPayload.entryUrl,
    runnerConfig.entry_url,
    runnerConfig.entryUrl,
  );
  if (runtimeBaseUrl) {
    runtimeContext.base_url = runtimeBaseUrl;
    runtimeContext.baseUrl = runtimeBaseUrl;
    runtimeContext['基础地址'] = runtimeBaseUrl;
  }
  if (runtimeEntryUrl) {
    runtimeContext.entry_url = runtimeEntryUrl;
    runtimeContext.entryUrl = runtimeEntryUrl;
    runtimeContext['页面入口'] = runtimeEntryUrl;
  }
  if (runnerPayload.env_name) {
    runtimeContext.env_name = runnerPayload.env_name;
    runtimeContext['执行环境'] = runnerPayload.env_name;
  }

  try {
    const emptyStepCases = runnerCases.filter((caseItem, index) => {
      const dsl = caseItem.dsl || {};
      const steps = Array.isArray(dsl.steps) ? dsl.steps : [];
      if (steps.length) {
        return false;
      }
      caseItem.__empty_step_reason = `${caseItem.node_title || dsl.ui_case_title || `用例${index + 1}`} 缺少测试步骤`;
      return true;
    });
    if (!runnerCases.length || emptyStepCases.length) {
      throw new Error(
        !runnerCases.length
          ? '本次任务没有可执行的 UI 自动化用例'
          : `UI 自动化用例缺少测试步骤：${emptyStepCases.map((item) => item.__empty_step_reason).join('；')}`,
      );
    }

    for (let caseIndex = 0; caseIndex < runnerCases.length; caseIndex += 1) {
      await assertRunNotCancelled(run, '用例开始前');
      const caseItem = runnerCases[caseIndex];
      const dsl = caseItem.dsl || {};
      const steps = Array.isArray(dsl.steps) ? dsl.steps : [];
      const caseTitle = String(caseItem.node_title || dsl.ui_case_title || `用例${caseIndex + 1}`);
      const casePath = String(caseItem.node_path || dsl.ui_case_path || '');
      const caseExtracted = {};
      const runtimeVariables = buildRuntimeVariables(dsl, extractedVariables, runtimeContext);
      const caseStartedAt = Date.now();
      let successStepCount = 0;
      let failedStepCount = 0;
      let caseError = '';

      const entryUrl = resolveEntryUrl(dsl, runtimeVariables);
    if (entryUrl) {
        await page.goto(
          normalizeGotoUrl(
            entryUrl,
            '用例入口 URL',
            String(runtimeVariables.base_url || runtimeVariables.baseUrl || runtimeVariables['基础地址'] || '').trim(),
          ),
          { waitUntil: 'domcontentloaded' },
        );
    }

      for (let stepIdx = 0; stepIdx < steps.length; stepIdx += 1) {
        await assertRunNotCancelled(run, `步骤${globalStepIndex + 1}开始前`);
        globalStepIndex += 1;
        try {
          const stepSummary = await executeDslStep(
            agent,
            page,
            run,
            steps[stepIdx],
            globalStepIndex,
            localRunDir,
            runtimeVariables,
            {
              case_index: caseItem.case_index || (caseIndex + 1),
              case_ref_id: caseItem.case_ref_id || 0,
              case_title: caseTitle,
              node_path: casePath,
              case_step_index: stepIdx + 1,
              multi_case: multiCase,
              dsl,
              extracted_store: caseExtracted,
              artifact_warnings: artifactWarnings,
              artifact_upload_queue: artifactUploadQueue,
            },
          );
          executedSteps.push(stepSummary);
          successStepCount += 1;
          await assertRunNotCancelled(run, `步骤${globalStepIndex}完成后`);
        } catch (error) {
          if (isRunCancelledError(error)) {
            throw error;
          }
          failedStepCount += 1;
          caseError = String(error?.stack || error?.message || error);
          if (error?.__argus_ui_step_summary) {
            executedSteps.push(error.__argus_ui_step_summary);
          }
          break;
        }
      }

      Object.assign(extractedVariables, caseExtracted);
      const skippedStepCount = Math.max(steps.length - successStepCount - failedStepCount, 0);
      caseResults.push({
        case_index: caseItem.case_index || (caseIndex + 1),
        case_ref_id: caseItem.case_ref_id || 0,
        file_title: caseItem.file_title || '',
        case_title: caseTitle,
        node_path: casePath,
        status: failedStepCount > 0 ? 'failed' : 'success',
        step_count: steps.length,
        success_step_count: successStepCount,
        failed_step_count: failedStepCount,
        skipped_step_count: skippedStepCount,
        elapsed_ms: Date.now() - caseStartedAt,
        error_message: caseError,
      });

      if (failedStepCount > 0) {
        const stopReason = `${caseTitle} 有步骤执行失败，已停止后续步骤和后续用例`;
        for (let skipIndex = caseIndex + 1; skipIndex < runnerCases.length; skipIndex += 1) {
          caseResults.push(buildSkippedCaseResult(runnerCases[skipIndex], skipIndex, stopReason));
        }
        throw new Error(caseError || stopReason);
      }
    }

    const elapsedMs = Date.now() - startedAt;
    const successCaseCount = caseResults.filter((item) => item.status === 'success').length;
    const failedCaseCount = caseResults.filter((item) => item.status === 'failed').length;
    const skippedCaseCount = caseResults.filter((item) => item.status === 'skipped').length;
    const finalStatus = failedCaseCount > 0 ? 'failed' : 'success';
    await fs.writeFile(
      localResultJsonPath,
      JSON.stringify({
        status: finalStatus,
        run_id: run.id,
        case_count: caseResults.length,
        case_results: caseResults,
        step_count: executedSteps.length,
        success_case_count: successCaseCount,
        failed_case_count: failedCaseCount,
        skipped_case_count: skippedCaseCount,
        artifact_warnings: artifactWarnings,
        elapsed_ms: elapsedMs,
      }, null, 2),
      'utf-8',
    );
    await fs.writeFile(
      localReportPath,
      buildHtmlReport(
        run,
        executedSteps,
        {
          status: finalStatus,
          message: failedCaseCount > 0
            ? `本次计划执行完成，共 ${caseResults.length} 个用例，成功 ${successCaseCount} 个，失败 ${failedCaseCount} 个，跳过 ${skippedCaseCount} 个。`
            : `本次计划执行成功，共 ${caseResults.length} 个用例全部通过。`,
        },
        '',
        caseResults,
      ),
      'utf-8',
    );
    await assertRunNotCancelled(run, '产物处理前');
    await markRunUploading(run, localRunDir, startedAt, caseResults, artifactWarnings, {
      execution_status: finalStatus,
      message: '步骤执行已完成，正在生成并上传录屏和报告产物。',
    });
    await context.close();
    const video = page.video();
    const localVideoPath = video ? await video.path() : null;
    await browser.close();

    enqueueUploadArtifact(artifactUploadQueue, run.id, run.report_path, localReportPath, 'text/html', artifactWarnings, '执行报告');
    if (localVideoPath) {
      enqueueUploadArtifact(artifactUploadQueue, run.id, run.video_path, localVideoPath, 'video/mp4', artifactWarnings, '录屏');
    }
    await artifactUploadQueue.drain();
    await fs.writeFile(
      localResultJsonPath,
      JSON.stringify({
        status: finalStatus,
        run_id: run.id,
        case_count: caseResults.length,
        case_results: caseResults,
        step_count: executedSteps.length,
        success_case_count: successCaseCount,
        failed_case_count: failedCaseCount,
        skipped_case_count: skippedCaseCount,
        artifact_warnings: artifactWarnings,
        elapsed_ms: elapsedMs,
      }, null, 2),
      'utf-8',
    );
    enqueueUploadArtifact(artifactUploadQueue, run.id, run.result_json_path, localResultJsonPath, 'application/json', artifactWarnings, '结果JSON');
    await artifactUploadQueue.drain();
    await assertRunNotCancelled(run, '产物上传完成后');

    if (finalStatus !== 'success') {
      throw new Error(`批次执行存在失败用例：${failedCaseCount}/${caseResults.length}`);
    }

    await saveRun({
      run_id: run.id,
      status: 'success',
      result_payload: {
        status: 'success',
        elapsed_ms: elapsedMs,
        local_run_dir: localRunDir,
        case_count: caseResults.length,
        case_results: caseResults,
        success_case_count: successCaseCount,
        failed_case_count: failedCaseCount,
        skipped_case_count: skippedCaseCount,
        artifact_warnings: artifactWarnings,
      },
      video_path: run.video_path,
      report_path: run.report_path,
      result_json_path: run.result_json_path,
      screenshot_dir: run.screenshot_dir,
      artifact_prefix: run.artifact_prefix,
    });
  } catch (error) {
    if (isRunCancelledError(error)) {
      const cancelMessage = String(error?.message || 'UI测试执行已被手动停止');
      artifactUploadQueue.cancel(cancelMessage);
      try {
        await context.close();
      } catch {}
      try {
        await browser.close();
      } catch {}
      const cancelCounts = summarizeCaseResults(caseResults);
      await saveRun({
        run_id: run.id,
        status: 'cancelled',
        error_message: cancelMessage,
        result_payload: {
          status: 'cancelled',
          message: cancelMessage,
          elapsed_ms: Date.now() - startedAt,
          local_run_dir: localRunDir,
          case_count: caseResults.length,
          case_results: caseResults,
          step_count: executedSteps.length,
          ...cancelCounts,
          artifact_warnings: artifactWarnings,
        },
        video_path: run.video_path,
        report_path: run.report_path,
        result_json_path: run.result_json_path,
        screenshot_dir: run.screenshot_dir,
        artifact_prefix: run.artifact_prefix,
      });
      return;
    }
    const failureErrorText = String(error?.stack || error?.message || error);
    const failureElapsedMs = Date.now() - startedAt;
    const failureCounts = summarizeCaseResults(caseResults);
    const successCaseCount = failureCounts.success_case_count;
    const failedCaseCount = failureCounts.failed_case_count;
    const skippedCaseCount = failureCounts.skipped_case_count;
    await markRunUploading(run, localRunDir, startedAt, caseResults, artifactWarnings, {
      execution_status: 'failed',
      error_message: failureErrorText,
      message: '步骤执行已结束，正在生成失败报告并上传截图、录屏和报告产物。',
    });
    let localVideoPath = null;
    try {
      const video = page.video();
      localVideoPath = video ? await video.path() : null;
    } catch {}
    try {
      await context.close();
    } catch {}
    try {
      await browser.close();
    } catch {}

    try {
      await fs.writeFile(
        localResultJsonPath,
        JSON.stringify({
          status: 'failed',
          run_id: run.id,
          case_count: caseResults.length,
          case_results: caseResults,
          step_count: executedSteps.length,
          success_case_count: successCaseCount,
          failed_case_count: failedCaseCount,
          skipped_case_count: skippedCaseCount,
          elapsed_ms: failureElapsedMs,
          artifact_warnings: artifactWarnings,
          error_message: failureErrorText,
        }, null, 2),
        'utf-8',
      );
    } catch {}
    try {
      await fs.writeFile(
        localReportPath,
        buildHtmlReport(
          run,
          executedSteps,
          {
            status: 'failed',
            message: caseResults.length
              ? `本次计划执行失败，共 ${caseResults.length} 个用例，成功 ${successCaseCount} 个，失败 ${failedCaseCount} 个，跳过 ${skippedCaseCount} 个。`
              : '本次UI执行失败。',
          },
          failureErrorText,
          caseResults,
        ),
        'utf-8',
      );
    } catch {}
    enqueueUploadArtifact(artifactUploadQueue, run.id, run.report_path, localReportPath, 'text/html', artifactWarnings, '执行报告');
    if (localVideoPath) {
      enqueueUploadArtifact(artifactUploadQueue, run.id, run.video_path, localVideoPath, 'video/mp4', artifactWarnings, '录屏');
    }
    await artifactUploadQueue.drain();
    await fs.writeFile(
      localResultJsonPath,
      JSON.stringify({
        status: 'failed',
        run_id: run.id,
        case_count: caseResults.length,
        case_results: caseResults,
        step_count: executedSteps.length,
        success_case_count: successCaseCount,
        failed_case_count: failedCaseCount,
        skipped_case_count: skippedCaseCount,
        elapsed_ms: failureElapsedMs,
        artifact_warnings: artifactWarnings,
        error_message: failureErrorText,
      }, null, 2),
      'utf-8',
    );
    enqueueUploadArtifact(artifactUploadQueue, run.id, run.result_json_path, localResultJsonPath, 'application/json', artifactWarnings, '结果JSON');
    await artifactUploadQueue.drain();

    await saveRun({
      run_id: run.id,
      status: 'failed',
      error_message: failureErrorText,
      result_payload: {
        status: 'failed',
        elapsed_ms: failureElapsedMs,
        local_run_dir: localRunDir,
        case_count: caseResults.length,
        case_results: caseResults,
        success_case_count: successCaseCount,
        failed_case_count: failedCaseCount,
        skipped_case_count: skippedCaseCount,
        artifact_warnings: artifactWarnings,
        error_message: failureErrorText,
      },
      video_path: run.video_path,
      report_path: run.report_path,
      result_json_path: run.result_json_path,
      screenshot_dir: run.screenshot_dir,
      artifact_prefix: run.artifact_prefix,
    });
  }
}

async function main() {
  await refreshBootstrapConfig();
  printStartupSummary();
  if (!hasRunnableConfig()) {
    console.log('[ui-runner] bootstrap or auth config missing, runner will stay idle and wait for config');
  } else {
    validateStartupConfig();
    if (!runtimeToken) {
      await loginAndGetToken();
    } else {
      console.log('[ui-runner] using configured token');
    }
    await hydrateMidsceneModelEnv();
    console.log('[ui-runner] preflight passed');
  }

  while (true) {
    try {
      await refreshBootstrapConfig();
      if (!hasRunnableConfig()) {
        await sleep(POLL_INTERVAL_MS);
        continue;
      }
      if (!runtimeToken) {
        await loginAndGetToken();
      }
      if (!midsceneModelReady) {
        await hydrateMidsceneModelEnv();
        console.log('[ui-runner] config detected, runner activated');
      }
      const task = await claimTask();
      if (!task) {
        emptyPollCount += 1;
        const waitMs = getIdlePollIntervalMs();
        if (emptyPollCount === 1 || emptyPollCount % 6 === 0) {
          console.log(`[ui-runner] 当前没有可领取的 UI 任务，${Math.round(waitMs / 1000)}s 后重试`);
        }
        await sleep(waitMs);
        continue;
      }
      emptyPollCount = 0;
      console.log(`[ui-runner] claimed run=${task.id} plan=${task.plan_id}`);
      await executeRun(task);
    } catch (error) {
      emptyPollCount = 0;
      console.error(`[ui-runner] loop failed: ${String(error?.message || error)}`);
      await sleep(POLL_INTERVAL_MS);
    }
  }
}

main().catch((error) => {
  console.error(`[ui-runner] startup failed: ${String(error?.message || error)}`);
  process.exit(1);
});
