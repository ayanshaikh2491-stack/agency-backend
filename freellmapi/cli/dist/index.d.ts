#!/usr/bin/env node
import { type ResolvedModel } from './models.js';
import type { CatalogModel } from './types.js';
interface CliOptions {
    url: string;
    apiKey?: string;
    profile: string;
    model?: string;
    dryRun: boolean;
    /** `doctor --timeout`: how long to wait for the /livez probe. */
    timeoutMs?: number;
    /** Positional arguments after the command. Only `doctor` takes any; every
     *  other command still rejects a second positional as it always has. */
    args: string[];
}
export declare function parseArgs(argv: string[]): {
    command?: string;
    options: CliOptions;
};
export interface Catalogs {
    available: CatalogModel[];
    full: CatalogModel[];
    /** Why the unfiltered fetch failed. Set means `full` is really the FILTERED
     *  roster, so "unknown model" and "out of quota" can no longer be told
     *  apart — the exact ambiguity this pair of fetches exists to remove. */
    degradedReason?: string;
}
/**
 * The available-only roster plus the unfiltered one.
 *
 * Only the unfiltered roster can distinguish "no such model" from "that model
 * exists but is out of quota right now", and reporting the second as the first
 * turns a rate limit into a spurious typo error. The unfiltered fetch is
 * best-effort: an older gateway that ignores the parameter, or any failure,
 * degrades to the filtered roster rather than blocking a launch — but it
 * RECORDS that it degraded, so the degradation can be reported instead of
 * silently reinstating the ambiguity.
 */
export declare function catalogs(url: string, apiKey: string): Promise<Catalogs>;
/**
 * Resolve a pinned `--model` ONCE, and say out loud anything that makes the
 * verdict less than certain.
 *
 * One resolution feeding both the id that gets pinned and the warning the user
 * reads, so the two can never disagree. Returns undefined when nothing was
 * pinned — the launcher then picks from the filtered roster, where there is no
 * ambiguity to lose and so nothing to warn about.
 */
export declare function resolvePinnedModel(requested: string | undefined, rosters: Catalogs, warn?: (message: string) => void): ResolvedModel | undefined;
export declare function claudeLaunchEnv(options: CliOptions, apiKey: string, models: CatalogModel[], baseEnv?: NodeJS.ProcessEnv, homeDir?: string, fullCatalog?: CatalogModel[]): NodeJS.ProcessEnv;
export declare function codexArgs(url: string, selected: string): string[];
export declare function main(argv?: string[]): Promise<number>;
export {};
