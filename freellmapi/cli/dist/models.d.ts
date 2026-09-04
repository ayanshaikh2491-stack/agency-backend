import type { CatalogModel } from './types.js';
export interface ResolvedModel {
    id: string;
    /** Context window in tokens, or undefined when the catalog does not publish
     *  one. Undefined is a fact — an unpublished window is not the same as a
     *  known 128k, and callers must not invent one. */
    contextWindow?: number;
    /** The catalog lists this model but it is not currently servable. */
    unavailable: boolean;
}
export declare class UnknownModelError extends Error {
}
export declare function contextWindowOf(model: CatalogModel): number | undefined;
/**
 * The launcher's default when the caller did not pin one: the first servable
 * concrete model, falling back to `auto`.
 *
 * Deliberately NOT the same default as the setup generators, which prefer
 * `auto`. A generated config file is long-lived and should let the router
 * choose per request; a launch pins one id into ANTHROPIC_MODEL for the whole
 * session, and naming a concrete model there is what makes the session's model
 * visible and reproducible. Unifying the two silently changes what
 * `freellmapi launch` runs.
 */
export declare function defaultModelId(models: CatalogModel[]): string;
/**
 * Resolve the model a launcher should pin.
 *
 * `available` is the filtered roster the CLI already fetches; `full` is the
 * unfiltered one. Pass the same array twice only when the unfiltered roster
 * could not be fetched — the caller then loses the ability to distinguish an
 * unknown id from an unavailable one, which is exactly the ambiguity this
 * function exists to remove.
 */
export declare function resolveLaunchModel(requested: string | undefined, available: CatalogModel[], full?: CatalogModel[]): ResolvedModel;
