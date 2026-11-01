const SEPARATOR = "; ";

/**
 * The composer text after picking `label` from one question's `options`.
 *
 * Picks append, so a single reply can answer several questions at once. A
 * second pick from the same question replaces the first: changing your mind
 * must not leave both answers in the reply, where "Fetch it; Answer without it"
 * reads as a refusal whichever was meant.
 */
export function withPickedOption(draft: string, options: string[], label: string): string {
  const kept = draft
    .split(SEPARATOR)
    .map((part) => part.trim())
    .filter((part) => part && !options.includes(part));
  return [...kept, label].join(SEPARATOR);
}
