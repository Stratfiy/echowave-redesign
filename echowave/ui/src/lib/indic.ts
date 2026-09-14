/** Whether text carries any Indian-script letters. Mirrors
 *  api/services/translation.is_indic: enough to say "written in an Indian
 *  language" without guessing which. */
const INDIC =
    /[ऀ-ॿঀ-৿਀-੿઀-૿଀-୿஀-௿ఀ-౿ಀ-೿ഀ-ൿ]/;

export function hasIndicScript(text: string | null | undefined): boolean {
    return INDIC.test(text || '');
}
