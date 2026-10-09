/** The 語氣 presets (backend/src/personality/presets/*.yaml). Names and descriptions come from the
 * `settings.personality.preset.<id>` messages, so adding a preset means adding it here and there. */
export const PERSONALITY_PRESET_IDS = ["steady", "coach", "concise", "data"] as const;
export type PersonalityPresetId = (typeof PERSONALITY_PRESET_IDS)[number];

/** Sent to the server to clear a chat's own pick, so it follows the account setting again. */
export const FOLLOW_ACCOUNT_TONE = "default";

export function isPersonalityPresetId(id: string): id is PersonalityPresetId {
  return (PERSONALITY_PRESET_IDS as readonly string[]).includes(id);
}
