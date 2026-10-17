'use strict';

// Fixture callbacks only. Process ownership, stop/disconnect and deadlines stay
// with the original caller; this helper never adopts or signals a process.
async function cleanupOwnedFleet(options) {
  const {agents, start, remote, disconnect, evidence, primaryError} = options;
  const hasPrimary = Object.prototype.hasOwnProperty.call(options, 'primaryError');
  const clean = [];
  let errors = [];
  for (let i = 0; i < agents.length; i += 1) {
    try {
      if (!agents[i].child) await start(i);
      clean.push({root: agents[i].root, port: agents[i].port, pids: agents[i].pids,
        result: await remote(i, '/agent/v1/stop', {})});
    } catch (error) {
      errors.push(error);
    } finally {
      try { await disconnect(i); } catch (error) { errors.push(error); }
      try { agents[i].log.end(); } catch (error) { errors.push(error); }
    }
  }
  try { evidence.note('owned_agents_cleanup', clean); } catch (error) { errors.push(error); }
  if (!errors.length) return {clean, errors, secondaryAttachmentFailed: false};

  let secondaryAttachmentFailed = false;
  if (hasPrimary) {
    try {
      const descriptor = Object.getOwnPropertyDescriptor(primaryError, 'secondary_errors');
      if (descriptor) {
        if (descriptor.enumerable || !('value' in descriptor) || !Array.isArray(descriptor.value)) {
          throw new TypeError('Original primary has incompatible secondary_errors');
        }
        descriptor.value.push(...errors);
        errors = descriptor.value;
      } else {
        Object.defineProperty(primaryError, 'secondary_errors', {value: errors, enumerable: false});
      }
    } catch (error) {
      secondaryAttachmentFailed = true;
      errors.push(error);
    }
  }
  try {
    evidence.note('owned_agents_cleanup_errors', {status: 'failed',
      secondary_attachment: hasPrimary ? (secondaryAttachmentFailed ? 'failed' : 'attached') : 'no_primary',
      errors: errors.map(error => ({name: error instanceof Error ? error.name : typeof error,
        message: error instanceof Error ? error.message : String(error)}))});
  } catch (error) { errors.push(error); }

  // The host's original catch/rethrow keeps its SAME primary error in flight.
  // Frozen/incompatible primaries keep that failure and bounded evidence; only
  // ordinary extensible Error objects are promised a new secondary_errors field.
  if (hasPrimary) return {clean, errors, secondaryAttachmentFailed};
  throw new AggregateError(errors, 'Owned fleet fixture cleanup failed');
}

module.exports = cleanupOwnedFleet;
