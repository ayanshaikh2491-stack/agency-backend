export type Verdict = 'routed' | 'degraded' | 'elsewhere' | 'shadowed' | 'unreachable' | 'unknown';
export interface Layer {
    /** Where the value came from, in the tool's own vocabulary. */
    source: string;
    value?: string;
    /** True for the layer whose value the tool will actually use. */
    effective: boolean;
}
export interface ToolReport {
    tool: string;
    verdict: Verdict;
    /** Every layer that set a value, highest precedence first. */
    layers: Layer[];
    /** The base URL the tool will really use, if one could be determined. */
    effectiveUrl?: string;
    /** For 'shadowed': the layer that beat the one the user probably edited. */
    shadowedBy?: string;
    /** Gateway identity, when the effective URL was probed. */
    gateway?: GatewayProbe;
    detail: string;
}
export interface GatewayProbe {
    reachable: boolean;
    /** Whether the response looks like this product. See probeGateway. */
    identified: boolean;
    /** Whether the gateway says it can actually serve requests right now. */
    healthy: boolean;
    /** The `status` field verbatim: 'ok', 'unavailable', or anything added later. */
    serviceStatus?: string;
    version?: string;
    /** HTTP status of the /livez response. */
    status?: number;
    error?: string;
}
/**
 * Normalize for comparison: scheme + host + port + path prefix, with any
 * trailing slash and `/v1` suffix removed.
 *
 * The path is KEPT, and kept case-sensitively. A gateway mounted under a prefix
 * (`http://host/gateway`) is a different endpoint from `http://host/other`, and
 * folding the path away would report the two as the same gateway. Only the
 * scheme and host are case-folded, because only those are case-insensitive.
 * Query and fragment are dropped: they never identify the endpoint.
 *
 * The result is also what the probe is built on, so it must stay a usable URL.
 */
export declare function normalizeUrl(url: string): string;
export declare function sameGateway(a: string | undefined, b: string | undefined): boolean;
/** The system directory holding Claude Code's MANAGED settings — the scope
 *  nothing else can override. */
export declare function managedSettingsDir(platform?: string): string;
/**
 * Managed settings files, HIGHEST PRECEDENCE FIRST.
 *
 * Not one file. The same directory also supports a `managed-settings.d/`
 * drop-in, whose `*.json` files are merged alphabetically ON TOP of the base
 * `managed-settings.json` — so a drop-in outranks the base, and the
 * alphabetically LAST drop-in outranks the ones before it. Reading only the
 * base file is how this command would confidently name the wrong effective
 * URL for exactly the fleet-managed install most likely to have one imposed:
 * it would report the layer the operator can see and miss the one actually
 * winning.
 *
 * An unreadable directory (the usual case — it does not exist) contributes
 * nothing rather than failing the report.
 */
export declare function managedSettingsPaths(platform?: string, dir?: string): string[];
/**
 * Claude Code's base-URL layers, HIGHEST PRECEDENCE FIRST.
 *
 * The ordering that matters, and the one it is easy to get backwards: a
 * settings file's `env` block BEATS the inherited shell environment. Claude
 * Code writes each `env` entry into the process environment at startup and
 * again when the file changes, replacing what the shell exported. So the
 * process environment is the LOWEST layer here, not the highest.
 *
 * The familiar "my launcher overrode my settings.json" case is not a
 * counter-example: a launcher that wins is writing MANAGED settings, which
 * outrank every other scope — note that the rest of that same `env` block
 * still applies, which is what makes the override so hard to see by reading
 * config alone.
 */
export declare function claudeLayers(env: NodeJS.ProcessEnv, homeDir: string, cwd?: string, platform?: string): Layer[];
/**
 * Codex's base-URL layers, highest precedence first.
 *
 * Codex resolves a PROVIDER, not a URL: `model_provider` names a key in the
 * `[model_providers]` table and that entry carries base_url. Reading only
 * `base_url` occurrences would report a provider the user is not selecting,
 * so the selected name is resolved first.
 */
export declare function codexLayers(env: NodeJS.ProcessEnv, homeDir: string): Layer[];
/** How long the /livez probe waits before calling an endpoint unreachable. */
export declare const DEFAULT_PROBE_TIMEOUT_MS = 5000;
/**
 * Probe a base URL's identity AND its health — two separate questions that must
 * not be collapsed.
 *
 * IDENTITY is structural: `/livez` answers `{status, version, uptime_s}` and
 * carries NO product identifier, so a body with that exact shape is evidence,
 * not proof — another service could serve the same three keys. `identified`
 * therefore means "responds the way this gateway responds", and the report says
 * so. Asserting identity harder would need either an authenticated call (the
 * CLI does not have a key here) or a product marker the server does not emit.
 *
 * HEALTH is the `status` VALUE, and it is deliberately not folded into
 * identity. The server really does emit `{status: 'unavailable', ...}` with a
 * 503 when the database or the encryption key is down (routes/status.ts), and
 * that response is still unmistakably this gateway. Requiring `status === 'ok'`
 * to identify it would report a correctly-routed but sick gateway as "probably
 * a different service on that port" — the confident wrong answer this command
 * exists to prevent. Anything that is not a 2xx `'ok'` is reported as
 * unhealthy and the raw status is echoed, so a status added later degrades into
 * a truthful "reaches this gateway, which says: <status>" instead of a lie.
 */
export declare function probeGateway(baseUrl: string, fetchImpl?: typeof fetch, timeoutMs?: number): Promise<GatewayProbe>;
export interface DoctorOptions {
    /** The gateway the user believes they are using (--url / FREELLMAPI_URL). */
    expectedUrl: string;
    env?: NodeJS.ProcessEnv;
    homeDir?: string;
    /** Project-scoped settings are resolved relative to this. */
    cwd?: string;
    fetchImpl?: typeof fetch;
    /** Probe timeout. Raise it on a slow link, where the default would report a
     *  reachable gateway as unreachable. */
    timeoutMs?: number;
}
export declare const DOCTOR_TOOLS: string[];
export declare function diagnose(tool: string, options: DoctorOptions): Promise<ToolReport>;
export declare function formatReport(report: ToolReport): string;
/** Exit code: 0 when every tool is routed, 1 otherwise — so it is usable in a
 *  script, not only by eye. */
export declare function exitCodeFor(reports: ToolReport[]): number;
