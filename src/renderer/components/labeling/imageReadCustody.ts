/** Gallery reads belong to the settled current source, or an exact evidence selection. */
export function imageReadAllowed(
  imagePath: string | null | undefined,
  sourceDirectory: string | null | undefined,
  externalSelectionPath: string | null | undefined,
  isProjectBusy: boolean,
): boolean {
  if (isProjectBusy || !imagePath) return false;
  if (externalSelectionPath === imagePath) return true;
  if (!sourceDirectory) return false;
  const spelling = (value: string) => value.replace(/\\/g, '/').replace(/\/+$/, '');
  return spelling(imagePath).startsWith(spelling(sourceDirectory) + '/');
}
