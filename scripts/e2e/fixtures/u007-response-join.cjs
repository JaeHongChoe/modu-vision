'use strict';

/** Join only pending original responses; keep every original terminal check.
 * `within` is the case's unchanged original-deadline helper, not a new clock.
 */
async function joinOriginalResponse(row, within) {
  if (row.finished === undefined)
    await within(row.done, row.deadline, 'full original response join');
  if (row.failure !== undefined) throw Error('Original response failed: ' + row.failure);
  if (!Number.isFinite(row.started) || !Number.isFinite(row.deadline)
      || !Number.isFinite(row.finished) || row.finished < row.started || row.finished > row.deadline)
    throw Error('Original response completion was absent or outside its original deadline');
  if (typeof row.body !== 'string' || !row.body || !Number.isInteger(row.size) || row.size < 0
      || row.size > 2 * 1024 * 1024 || typeof row.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(row.sha256))
    throw Error('Original full response body receipt is absent or invalid');
  if (!Number.isInteger(row.status) || row.status < 200 || row.status >= 300)
    throw Error('Original response status is outside the unchanged success gate');
}

module.exports = {joinOriginalResponse};
