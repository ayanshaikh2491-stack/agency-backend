import type { GeneratedFile } from './types.js';
export declare function renderFile(file: GeneratedFile, existing?: string): string;
export interface ApplyResult {
    path: string;
    changed: boolean;
    backupPath?: string;
    rendered: string;
    previous: string;
}
export declare function applyGeneratedFiles(files: GeneratedFile[], dryRun: boolean): ApplyResult[];
export declare function applyGeneratedFile(file: GeneratedFile, dryRun: boolean): ApplyResult;
export declare function printDryRunDiff(result: ApplyResult): string;
